"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { PrivyProvider, usePrivy } from "@privy-io/react-auth";
import { toSolanaWalletConnectors } from "@privy-io/react-auth/solana";

import { setAuthTokenAccessor } from "@/lib/api/client";

interface AuthTokenBridgeProps {
  children: ReactNode;
}

// Inside the provider so usePrivy() works. Registers the token accessor
// once on mount; the SDK's getAccessToken handles caching + refresh.
//
// The accessor *awaits* Privy's initial hydration before deciding on a
// token, rather than bailing to null while ready === false. Without this
// gate, requests fired in the same paint as AuthModalGate flipping its
// children visible can slip through with no Authorization header — React
// fires the children's useEffects before this bridge's effect re-runs to
// register a real-token accessor, and the backend then 401s those calls.
// The gate resolves exactly once (the first time ready flips to true)
// and stays resolved, so it adds zero overhead to steady-state requests.
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

  // Mirror authenticated into a ref so a single registered accessor sees
  // current state without needing the effect below to re-run on every
  // sign-in / sign-out flip.
  const authenticatedRef = useRef(authenticated);
  authenticatedRef.current = authenticated;

  useEffect(() => {
    setAuthTokenAccessor(async () => {
      await readyGateRef.current!.promise;
      if (!authenticatedRef.current) return null;
      const token = await getAccessToken();
      return token ?? null;
    });
    return () => {
      setAuthTokenAccessor(null);
    };
  }, [getAccessToken]);

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
