"use client";

import { type ReactNode } from "react";
import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";

import { isPrivyConfigured } from "@/lib/auth/config";
import { isPublicRoute } from "@/components/auth/publicRoutes";

// AuthModal is only rendered when we actually need to gate, so the
// dynamic import keeps it out of the open-mode bundle entirely.
const AuthModalGate = dynamic(() => import("./AuthModalGate"), { ssr: false });

interface AuthGateProps {
  children: ReactNode;
}

/**
 * Single source of truth for whether the studio is reachable.
 *
 * Open-mode (no NEXT_PUBLIC_PRIVY_APP_ID) → renders children directly,
 * never mounts the SDK-using overlay. Configured + on a public route
 * (`/r/[runId]`, `embed/*`) → also renders children directly. Anywhere
 * else, defers to AuthModalGate which subscribes to usePrivy() and
 * portals the modal on top of the route shell when the user is anon.
 */
export default function AuthGate({ children }: AuthGateProps) {
  const pathname = usePathname() ?? "/";

  if (!isPrivyConfigured() || isPublicRoute(pathname)) {
    return <>{children}</>;
  }

  return <AuthModalGate pathname={pathname}>{children}</AuthModalGate>;
}
