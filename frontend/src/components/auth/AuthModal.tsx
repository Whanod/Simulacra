"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { useRouter } from "next/navigation";
import { useLoginWithEmail, usePrivy } from "@privy-io/react-auth";
import { useWallets as useSolanaWallets } from "@privy-io/react-auth/solana";

import { authReducer, initialAuthState, type AuthAction } from "./authReducer";

const RESEND_SECONDS = 60;
const SUCCESS_BEAT_MS = 600;
const WALLET_PROVISION_TIMEOUT_MS = 3_000;

interface AuthModalProps {
  initialPath: string;
}

/**
 * Stateful UI wired to Privy's email-OTP hooks. The state machine lives
 * in `./authReducer.ts` so the unit tests can drive it directly without
 * mocking the SDK; this component is the SDK + DOM surface.
 */
export default function AuthModal({ initialPath }: AuthModalProps) {
  const router = useRouter();
  const { sendCode, loginWithCode } = useLoginWithEmail();
  const { authenticated } = usePrivy();
  const { wallets: solanaWallets } = useSolanaWallets();
  const embeddedWallet = solanaWallets[0] ?? null;

  const [state, dispatch] = useReducer(authReducer, initialAuthState);

  // Resend countdown ticker — only ticks while we're awaiting OTP.
  useEffect(() => {
    if (state.kind !== "awaitingOtp") return;
    if (state.resendIn <= 0) return;
    const id = window.setInterval(() => {
      dispatch({ type: "RESEND_TICK" });
    }, 1_000);
    return () => window.clearInterval(id);
  }, [state]);

  // Auto-submit the OTP once 6 digits are entered. Depending on the
  // joined digits + submitting flag (rather than the whole `state`)
  // keeps this effect from re-running every time RESEND_TICK fires.
  const otpJoined = state.kind === "awaitingOtp" ? state.digits.join("") : "";
  const otpSubmitting = state.kind === "awaitingOtp" ? state.submitting : false;
  useEffect(() => {
    if (state.kind !== "awaitingOtp") return;
    if (otpJoined.length !== 6) return;
    if (otpSubmitting) return;
    void verifyCode(otpJoined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.kind, otpJoined, otpSubmitting]);

  // Success beat → embedded wallet wait → close / redirect.
  useEffect(() => {
    if (state.kind !== "success") return;
    let cancelled = false;
    const start = Date.now();
    const timer = window.setTimeout(async () => {
      // Wait for the embedded wallet to land, capped at 3s. Privy
      // resolves the wallet asynchronously after `authenticated` flips,
      // and we'd rather block the redirect than land the user on
      // /dashboard with no wallet.
      while (
        !embeddedWallet &&
        Date.now() - start < WALLET_PROVISION_TIMEOUT_MS &&
        !cancelled
      ) {
        await new Promise((r) => setTimeout(r, 50));
      }
      if (cancelled) return;
      // Redirect rule: if the user landed on `/`, push them to
      // /dashboard. Otherwise close in place — they came in via a
      // deep-link or were re-authing after sign-out.
      if (initialPath === "/") {
        router.replace("/dashboard");
      }
      dispatch({ type: "RESET" });
    }, SUCCESS_BEAT_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [state.kind, embeddedWallet, initialPath, router]);

  const verifyCode = useCallback(
    async (code: string) => {
      dispatch({ type: "VERIFY_BEGIN" });
      try {
        await loginWithCode({ code });
        dispatch({ type: "VERIFY_OK" });
      } catch (err) {
        dispatch({
          type: "VERIFY_FAIL",
          error:
            err instanceof Error
              ? "That code didn't match. Try again."
              : "We couldn't reach Privy. Check your connection.",
        });
      }
    },
    [loginWithCode],
  );

  const handleSendCode = useCallback(
    async (email: string) => {
      dispatch({ type: "SEND_BEGIN", email });
      try {
        await sendCode({ email });
        dispatch({ type: "SEND_OK", resendIn: RESEND_SECONDS });
      } catch {
        dispatch({
          type: "SEND_FAIL",
          error: "We couldn't send a code to that email. Try again?",
        });
      }
    },
    [sendCode],
  );

  // Belt-and-suspenders: if Privy reports authenticated while we're
  // still on the email screen (session restored mid-flow), fast-forward
  // to success so the gate doesn't get stuck.
  useEffect(() => {
    if (authenticated && state.kind !== "success") {
      dispatch({ type: "VERIFY_OK" });
    }
  }, [authenticated, state.kind]);

  return (
    <ModalShell>
      {state.kind === "email" && (
        <EmailScreen
          email={state.email}
          error={state.error}
          submitting={state.submitting}
          onChange={(email) => dispatch({ type: "EMAIL_CHANGE", email })}
          onSubmit={() => handleSendCode(state.email)}
        />
      )}
      {state.kind === "awaitingOtp" && (
        <OtpScreen
          email={state.email}
          digits={state.digits}
          error={state.error}
          submitting={state.submitting}
          resendIn={state.resendIn}
          onDigitsChange={(digits) => dispatch({ type: "OTP_CHANGE", digits })}
          onBack={() => dispatch({ type: "EDIT_EMAIL" })}
          onResend={() => handleSendCode(state.email)}
          onVerify={() => verifyCode(state.digits.join(""))}
        />
      )}
      {state.kind === "success" && <SuccessScreen />}
    </ModalShell>
  );
}

// ── presentational pieces (no SDK) ──────────────────────────────────────

function ModalShell({ children }: { children: React.ReactNode }) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  // Backdrop pointerdown — privy.md §5.11 mandates a no-op. We swallow
  // the event before focus moves and snap focus back into the card so
  // the active OTP / email input keeps caret + selection state.
  const handleBackdropPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    e.preventDefault();
    const card = cardRef.current;
    if (!card) return;
    const active = document.activeElement as HTMLElement | null;
    if (active && card.contains(active)) return;
    const target = card.querySelector<HTMLElement>(
      'input:not([disabled]), button:not([disabled])',
    );
    target?.focus();
  };
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-modal-title"
      onPointerDown={handleBackdropPointerDown}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(28, 25, 22, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
      onKeyDown={(e) => {
        // Escape is a no-op while the gate is up — see privy.md §5.11.
        if (e.key === "Escape") e.preventDefault();
      }}
    >
      <div
        ref={cardRef}
        style={{
          background: "#fff",
          width: 360,
          maxWidth: "calc(100% - 32px)",
          padding: 24,
          borderRadius: 6,
          border: "1px solid #1c1916",
          boxShadow: "0 18px 40px -18px rgba(0,0,0,0.6)",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        {children}
      </div>
    </div>
  );
}

function EmailScreen({
  email,
  error,
  submitting,
  onChange,
  onSubmit,
}: {
  email: string;
  error: string | null;
  submitting: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (valid && !submitting) onSubmit();
      }}
    >
      <h2 id="auth-modal-title" style={{ margin: "0 0 4px", fontSize: 20 }}>
        Sign in to Simulacra
      </h2>
      <p style={{ margin: "0 0 16px", color: "#6b655c", fontSize: 13 }}>
        Save scenarios, compare runs, share results.
      </p>
      <label
        htmlFor="auth-email"
        style={{
          display: "block",
          fontSize: 11,
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          color: "#6b655c",
          marginBottom: 6,
        }}
      >
        Email
      </label>
      <input
        ref={inputRef}
        id="auth-email"
        name="email"
        type="email"
        autoComplete="email"
        value={email}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%",
          padding: "8px 10px",
          fontSize: 14,
          border: "1px solid #d6cfc0",
          borderRadius: 3,
        }}
      />
      {error && (
        <p style={{ color: "#7a1610", fontSize: 12, marginTop: 6 }}>{error}</p>
      )}
      <button
        type="submit"
        disabled={!valid || submitting}
        style={{
          width: "100%",
          marginTop: 14,
          padding: "10px 12px",
          background: valid && !submitting ? "#1c1916" : "#a6a097",
          color: "#fff",
          border: 0,
          borderRadius: 3,
          fontSize: 13,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          cursor: valid && !submitting ? "pointer" : "not-allowed",
        }}
      >
        Continue with email
      </button>
      <p style={{ marginTop: 12, fontSize: 11, color: "#6b655c", textAlign: "center" }}>
        By continuing you agree to terms · privacy
      </p>
    </form>
  );
}

function OtpScreen({
  email,
  digits,
  error,
  submitting,
  resendIn,
  onDigitsChange,
  onBack,
  onResend,
  onVerify,
}: {
  email: string;
  digits: string[];
  error: string | null;
  submitting: boolean;
  resendIn: number;
  onDigitsChange: (digits: string[]) => void;
  onBack: () => void;
  onResend: () => void;
  onVerify: () => void;
}) {
  const cellRefs = useRef<Array<HTMLInputElement | null>>([]);
  useEffect(() => {
    // Focus the first empty cell on mount / after edits.
    const idx = digits.findIndex((d) => d === "");
    cellRefs.current[idx === -1 ? 5 : idx]?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCellChange = (index: number, value: string) => {
    // Pasted full code into any single cell → distribute across all.
    if (value.length > 1) {
      const cleaned = value.replace(/\D/g, "").slice(0, 6).padEnd(6, "");
      const next = cleaned.split("");
      onDigitsChange(next);
      cellRefs.current[5]?.focus();
      return;
    }
    if (!/^\d?$/.test(value)) return;
    const next = digits.slice();
    next[index] = value;
    onDigitsChange(next);
    if (value && index < 5) cellRefs.current[index + 1]?.focus();
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !digits[index] && index > 0) {
      cellRefs.current[index - 1]?.focus();
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        style={{
          background: "none",
          border: 0,
          color: "#6b655c",
          fontSize: 12,
          cursor: "pointer",
          padding: 0,
          marginBottom: 8,
        }}
      >
        ← Edit email
      </button>
      <h2 id="auth-modal-title" style={{ margin: "0 0 4px", fontSize: 20 }}>
        Check your email
      </h2>
      <p style={{ margin: "0 0 16px", color: "#6b655c", fontSize: 13 }}>
        Code sent to <strong style={{ color: "#1c1916" }}>{email}</strong>
      </p>
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        {digits.map((digit, idx) => (
          <input
            key={idx}
            ref={(el) => {
              cellRefs.current[idx] = el;
            }}
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={6 /* allow paste into a single cell */}
            value={digit}
            aria-label={`Digit ${idx + 1} of 6`}
            onChange={(e) => handleCellChange(idx, e.target.value)}
            onKeyDown={(e) => handleKeyDown(idx, e)}
            disabled={submitting}
            style={{
              flex: 1,
              minWidth: 0,
              height: 44,
              textAlign: "center",
              fontSize: 18,
              border: "1px solid #d6cfc0",
              borderRadius: 3,
              fontFamily: "ui-monospace, monospace",
            }}
          />
        ))}
      </div>
      {/* Hidden Verify button — auto-submit handles sighted users; this
          stays focusable for assistive tech that prefers an explicit
          activation. */}
      <button
        type="button"
        onClick={onVerify}
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
        disabled={digits.join("").length !== 6 || submitting}
      >
        Verify
      </button>
      {error && (
        <p style={{ color: "#7a1610", fontSize: 12, margin: "4px 0 0" }}>{error}</p>
      )}
      <p style={{ marginTop: 12, fontSize: 11, color: "#6b655c", textAlign: "center" }}>
        Didn&apos;t get it?{" "}
        {resendIn > 0 ? (
          <>Resend in 0:{resendIn.toString().padStart(2, "0")}</>
        ) : (
          <button
            type="button"
            onClick={onResend}
            style={{
              background: "none",
              border: 0,
              padding: 0,
              color: "#1c1916",
              textDecoration: "underline",
              cursor: "pointer",
            }}
          >
            Resend code
          </button>
        )}
      </p>
    </div>
  );
}

function SuccessScreen() {
  return (
    <div style={{ textAlign: "center", padding: "20px 0" }}>
      <div
        aria-hidden
        style={{
          width: 40,
          height: 40,
          borderRadius: "50%",
          background: "#1e4d33",
          color: "#fff",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 22,
          marginBottom: 12,
        }}
      >
        ✓
      </div>
      <h2 id="auth-modal-title" style={{ margin: "0 0 4px", fontSize: 20 }}>
        You&apos;re in
      </h2>
      <p style={{ margin: 0, color: "#6b655c", fontSize: 13 }}>Redirecting…</p>
    </div>
  );
}

// re-export AuthAction for tests
export type { AuthAction };
