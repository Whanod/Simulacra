"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePrivy } from "@privy-io/react-auth";

import { isPrivyConfigured } from "@/lib/auth/config";

/**
 * Small avatar + email pill for the topbar. Renders nothing in open
 * mode (no Privy) or when the user is signed out — Flow 01's gate is
 * the canonical sign-in surface, so we don't expose a "Sign in" button
 * elsewhere.
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
    <div ref={containerRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={email}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 10px 4px 4px",
          border: "1px solid #d6cfc0",
          borderRadius: 999,
          background: "#fdfcf8",
          fontSize: 12,
          cursor: "pointer",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 20,
            height: 20,
            borderRadius: "50%",
            background: "linear-gradient(135deg,#0f4c5c 0%,#9945ff 100%)",
            color: "#fff",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 10,
            fontWeight: 600,
          }}
        >
          {initial}
        </span>
        <span style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {email}
        </span>
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 4px)",
            background: "#fff",
            border: "1px solid #d6cfc0",
            borderRadius: 4,
            minWidth: 160,
            boxShadow: "0 8px 20px -10px rgba(0,0,0,0.3)",
            padding: 4,
            zIndex: 50,
          }}
        >
          <Link
            href="/settings/wallets"
            role="menuitem"
            onClick={() => setOpen(false)}
            style={{ display: "block", padding: "6px 10px", color: "#1c1916", textDecoration: "none", fontSize: 13 }}
          >
            Settings
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={async () => {
              setOpen(false);
              await logout();
            }}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              padding: "6px 10px",
              border: 0,
              background: "none",
              color: "#1c1916",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
