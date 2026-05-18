"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { PrivyProvider, usePrivy } from "@privy-io/react-auth";
import { toSolanaWalletConnectors } from "@privy-io/react-auth/solana";

import { setAuthTokenAccessor } from "@/lib/api/client";

interface AuthTokenBridgeProps {
  children: ReactNode;
}

// Inside the provider so usePrivy() works. Registers the token accessor
// exactly once on mount; the SDK's getAccessToken handles caching +
// refresh.
//
// All dynamic values (getAccessToken, authenticated) are read through
// refs so the single registered accessor always sees current state
// without re-running its effect — and crucially, without the brief
// cleanup→setup window where the accessor would be null and any in-
// flight apiFetch would be unblocked with no token. The accessor also
// awaits Privy's initial hydration via a one-shot promise: requests
// fired in the same paint that AuthModalGate flips its children visible
// reach apiFetch BEFORE this bridge's effects fire (React commits
// bottom-up), so they have to block on a gate rather than bail to null.
function AuthTokenBridge({ children }: AuthTokenBridgeProps) {
  const { getAccessToken, ready, authenticated } = usePrivy();

  const readyGateRef = useRef<{ promise: Promise<void>; resolve: () => void } | null>(null);
  if (readyGateRef.current === null) {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => {
      resolve = r;
    });
    readyGateRef.current = { promise, resolve };
  }
  useEffect(() => {
    if (ready) readyGateRef.current!.resolve();
  }, [ready]);

  // Refs let one stable accessor read the latest values. Some Privy
  // releases return a fresh getAccessToken on every render — closing
  // over the mount-time reference would mean stale-fn calls returning
  // null once the SDK swapped it out.
  const getAccessTokenRef = useRef(getAccessToken);
  getAccessTokenRef.current = getAccessToken;
  const authenticatedRef = useRef(authenticated);
  authenticatedRef.current = authenticated;

  useEffect(() => {
    setAuthTokenAccessor(async () => {
      await readyGateRef.current!.promise;
      if (!authenticatedRef.current) return null;
      const token = await getAccessTokenRef.current();
      return token ?? null;
    });
    return () => {
      setAuthTokenAccessor(null);
    };
    // Intentionally empty deps: the accessor is read through refs, so a
    // single registration is correct for the bridge's lifetime. Adding
    // getAccessToken / authenticated to deps reintroduces the cleanup
    // window where the accessor flickers to null between renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <>{children}</>;
}

interface PrivyAuthRootProps {
  appId: string;
  children: ReactNode;
}

export default function PrivyAuthRoot({ appId, children }: PrivyAuthRootProps) {
  return (
    <PrivyProvider
      appId={appId}
      config={{
        loginMethods: ["email"],
        appearance: { theme: "light" },
        embeddedWallets: {
          // Provision a Solana embedded wallet on first sign-in. Users
          // who linked an external wallet later (via /settings/wallets)
          // skip the auto-provision.
          solana: { createOnLogin: "users-without-wallets" },
        },
        externalWallets: {
          solana: { connectors: toSolanaWalletConnectors() },
        },
      }}
    >
      <AuthTokenBridge>{children}</AuthTokenBridge>
    </PrivyProvider>
  );
}
