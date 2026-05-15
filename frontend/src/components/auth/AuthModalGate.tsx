"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { usePrivy } from "@privy-io/react-auth";

import AuthModal from "@/components/auth/AuthModal";

interface AuthModalGateProps {
  pathname: string;
  children: ReactNode;
}

const HISTORY_SENTINEL = "auth-gate";

/**
 * Subscribes to Privy's hydration + auth state and decides whether to
 * paint the blocking modal on top of the route shell.
 *
 * The shell underneath stays mounted in all cases — we never swap
 * routes — so a successful auth restores the user to exactly the page
 * they landed on.
 */
export default function AuthModalGate({ pathname, children }: AuthModalGateProps) {
  const { ready, authenticated } = usePrivy();
  // Capture the path at first mount so the success-state effect knows
  // whether to redirect to /dashboard or close the modal in place.
  const initialPathRef = useRef<string>(pathname);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  // Body scroll + history.back() guard live here so the lifecycle ties
  // exactly to the gated window; both invariants come from privy.md
  // §5.11 ("history.back() while gated is a no-op").
  useEffect(() => {
    const showOverlay = ready && !authenticated;
    if (!showOverlay) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Push a sentinel state so the next browser-back triggers popstate
    // here instead of unloading the gated route. On popstate we re-push
    // the sentinel — net effect: back button is inert while gated.
    window.history.pushState({ __gate: HISTORY_SENTINEL }, "");
    const onPopState = () => {
      window.history.pushState({ __gate: HISTORY_SENTINEL }, "");
    };
    window.addEventListener("popstate", onPopState);

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("popstate", onPopState);
      // Pop the sentinel we pushed so we don't leave a phantom entry
      // behind once the user authenticates.
      if (window.history.state?.__gate === HISTORY_SENTINEL) {
        window.history.back();
      }
    };
  }, [ready, authenticated]);

  // Real focus trap: `inert` neutralises the shell, but a JS Tab guard
  // on the overlay catches the tail-end Tab/Shift-Tab cases that try to
  // jump to the browser chrome.
  useEffect(() => {
    const showOverlay = ready && !authenticated;
    if (!showOverlay) return;

    const overlay = overlayRef.current;
    if (!overlay) return;

    const focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusables = Array.from(
        overlay.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && (active === first || !overlay.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !overlay.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [ready, authenticated]);

  if (!ready) {
    // Hydrating — render the route shell behind a transparent layer so
    // the modal doesn't flash before Privy has had a chance to restore
    // an existing session. Pointer events on the shell are disabled to
    // avoid letting the user click into a half-loaded gate.
    return (
      <>
        <div aria-hidden="true" style={{ pointerEvents: "none" }}>{children}</div>
      </>
    );
  }

  if (authenticated) {
    return <>{children}</>;
  }

  return (
    <>
      {/* Render the shell underneath inert so screen readers and Tab
          stay inside the modal. inert is supported in all evergreen
          browsers as of 2024; the JS Tab guard above catches the tail
          cases where focus tries to escape to the browser chrome.
          React 19's JSX types include `inert` as a boolean prop — pass
          `true` (not `""`) so React serialises it as a real HTML
          attribute the [inert] selector can match. */}
      <div
        ref={shellRef}
        // @ts-expect-error inert was added to React JSX types late; some
        // setups still ship the older @types/react that lacks it.
        inert={true}
        data-testid="auth-gate-shell"
      >
        {children}
      </div>
      <div ref={overlayRef}>
        <AuthModal initialPath={initialPathRef.current} />
      </div>
    </>
  );
}
