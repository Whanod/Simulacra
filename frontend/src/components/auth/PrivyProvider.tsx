"use client";

import { useEffect, type ReactNode } from "react";
import dynamic from "next/dynamic";

import { PRIVY_APP_ID, isPrivyConfigured } from "@/lib/auth/config";
import { setAuthTokenAccessor } from "@/lib/api/client";

// Privy's React provider is loaded dynamically so the open-mode shim
// doesn't pull the SDK into the bundle when NEXT_PUBLIC_PRIVY_APP_ID is
// unset (vitest, local dev, public build with no auth). The dynamic
// import resolves to a lightweight wrapper that registers the token
// accessor with apiFetch.
const PrivyAuthRoot = dynamic(() => import("./PrivyAuthRoot"), {
  ssr: false,
});

export default function AppPrivyProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    // Open-mode shim: when Privy is not configured, ensure the api
    // client always falls back to anonymous regardless of any state
    // left over from a hot-reload that flipped the env var.
    if (!isPrivyConfigured()) {
      setAuthTokenAccessor(null);
    }
  }, []);

  if (!isPrivyConfigured()) {
    // No provider, no overlay — every fetch goes out unauthenticated and
    // the backend's open-mode contract serves the response. This is the
    // path every existing test runs in.
    return <>{children}</>;
  }

  return <PrivyAuthRoot appId={PRIVY_APP_ID!}>{children}</PrivyAuthRoot>;
}
