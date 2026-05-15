"use client";

import { useEffect, type ReactNode } from "react";
import { PrivyProvider, usePrivy } from "@privy-io/react-auth";
import { toSolanaWalletConnectors } from "@privy-io/react-auth/solana";

import { setAuthTokenAccessor } from "@/lib/api/client";

interface AuthTokenBridgeProps {
  children: ReactNode;
}

// Inside the provider so usePrivy() works. Registers the token accessor
// once on mount; the SDK's getAccessToken handles caching + refresh.
function AuthTokenBridge({ children }: AuthTokenBridgeProps) {
  const { getAccessToken, ready, authenticated } = usePrivy();

  useEffect(() => {
    setAuthTokenAccessor(async () => {
      // getAccessToken returns null when there is no session. When the
      // SDK is mid-hydration (`ready === false`) we also bail to null so
      // the very first request after a page load isn't blocked waiting
      // on Privy. Subsequent requests (after the session restores) pick
      // up the real token.
      if (!ready) return null;
      if (!authenticated) return null;
      const token = await getAccessToken();
      return token ?? null;
    });
    return () => {
      setAuthTokenAccessor(null);
    };
  }, [getAccessToken, ready, authenticated]);

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
