"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePrivy } from "@privy-io/react-auth";

import { isPrivyConfigured } from "@/lib/auth/config";

/**
 * Avatar + email pill for the topbar. Renders nothing in open mode (no
 * Privy) or when the user is signed out — the gate is the canonical
 * sign-in surface, so we don't expose a "Sign in" button elsewhere.
 *
 * Styles live in globals.css under `.user-chip*` so the chrome matches
 * the dark studio aesthetic (var(--bg-*), var(--accent), etc.).
 */
export default function UserChip() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!isPrivyConfigured()) return null;
  return <UserChipInner open={open} setOpen={setOpen} containerRef={ref} />;
}

function UserChipInner({
  open,
  setOpen,
  containerRef,
}: {
  open: boolean;
  setOpen: (v: boolean) => void;
  containerRef: React.MutableRefObject<HTMLDivElement | null>;
}) {
  const { user, authenticated, logout } = usePrivy();
  if (!authenticated || !user) return null;

  const email =
    typeof user.email?.address === "string" ? user.email.address : "signed in";
  const initial = email.charAt(0).toUpperCase();

  return (
    <div ref={containerRef} className="user-chip">
      <button
        type="button"
        className="user-chip__btn"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={email}
      >
        <span aria-hidden className="user-chip__avatar">
          {initial}
        </span>
        <span className="user-chip__email">{email}</span>
      </button>
      {open && (
        <div role="menu" className="user-chip__menu">
          <div className="user-chip__menu-header">
            Signed in as
            <strong title={email}>{email}</strong>
          </div>
          <Link
            href="/settings/wallets"
            role="menuitem"
            onClick={() => setOpen(false)}
          >
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            className="user-chip__menu-danger"
            onClick={async () => {
              setOpen(false);
              await logout();
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
