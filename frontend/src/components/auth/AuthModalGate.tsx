"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { usePrivy } from "@privy-io/react-auth";

import AuthModal from "@/components/auth/AuthModal";

interface AuthModalGateProps {
  pathname: string;
  children: ReactNode;
}

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

  // Keep focus + scroll predictable while gated: lock body scroll only
  // when the overlay is up.
  useEffect(() => {
    const showOverlay = ready && !authenticated;
    if (!showOverlay) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
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
          browsers as of 2024 and is the cleanest way to neutralise an
          existing tree without rebuilding it. */}
      {/* @ts-expect-error inert is a valid HTML attribute, types lag */}
      <div inert="">{children}</div>
      <AuthModal initialPath={initialPathRef.current} />
    </>
  );
}
