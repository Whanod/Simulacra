"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { usePrivy } from "@privy-io/react-auth";

import AuthModal from "@/components/auth/AuthModal";

interface AuthModalGateProps {
  pathname: string;
  children: ReactNode;
}

/**
 * Gate decision layer. When the user is anonymous on a gated route we
 * render the sign-in screen *instead of* the studio shell — no broken
 * data fetches behind a translucent overlay, no inert dance, no
 * focus-trap rigging. The studio mounts only once Privy reports the
 * caller as authenticated.
 *
 * The component is loaded via `next/dynamic({ ssr: false })` from
 * AuthGate, so all of `usePrivy`'s React-only state stays out of the
 * server render.
 */
export default function AuthModalGate({ pathname, children }: AuthModalGateProps) {
  const { ready, authenticated } = usePrivy();
  // Capture the path at first mount so the success effect knows
  // whether to redirect to /dashboard or close in place.
  const initialPathRef = useRef<string>(pathname);

  // Body-scroll lock while the sign-in screen is up. A long page (e.g.
  // a deep-linked dashboard) would otherwise let users scroll the
  // (unmounted) shell behind the screen via momentum on iOS Safari.
  useEffect(() => {
    if (ready && !authenticated) {
      const previous = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = previous;
      };
    }
    return undefined;
  }, [ready, authenticated]);

  if (!ready) {
    // Privy is hydrating. Render a quiet placeholder using the studio's
    // background colour so there's no flash between hydrate-pending and
    // signed-in/out resolution. The class is shared with the sign-in
    // screen for consistency.
    return (
      <div
        aria-hidden="true"
        data-testid="auth-hydrating"
        className="auth-page"
      />
    );
  }

  if (!authenticated) {
    return <AuthModal initialPath={initialPathRef.current} />;
  }

  return <>{children}</>;
}
