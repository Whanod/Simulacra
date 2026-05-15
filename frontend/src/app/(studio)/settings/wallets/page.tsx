"use client";

import { useState } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { useWallets as useSolanaWallets } from "@privy-io/react-auth/solana";

import Topbar from "@/components/shell/Topbar";

/**
 * Wallets settings page (Privy v1, plan §5.9). Shown only to signed-in
 * users via the UserChip popover; not part of onboarding so the gate
 * stays strictly email-only.
 */
export default function WalletsSettingsPage() {
  const { ready, authenticated, linkWallet } = usePrivy();
  const { wallets } = useSolanaWallets();
  const embedded = wallets[0] ?? null;
  const [copied, setCopied] = useState(false);

  if (!ready) {
    return (
      <main style={{ padding: 24 }}>
        <Topbar title="Wallets" />
        <p style={{ color: "#6b655c" }}>Loading…</p>
      </main>
    );
  }

  if (!authenticated) {
    return (
      <main style={{ padding: 24 }}>
        <Topbar title="Wallets" />
        <p style={{ color: "#6b655c" }}>Sign in to manage wallets.</p>
      </main>
    );
  }

  return (
    <main style={{ padding: 24, maxWidth: 720 }}>
      <Topbar title="Wallets" />
      <section style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>Embedded Solana wallet</h2>
        {embedded ? (
          <div
            style={{
              border: "1px solid #d6cfc0",
              borderRadius: 6,
              padding: 16,
              display: "flex",
              alignItems: "center",
              gap: 12,
            }}
          >
            <code style={{ flex: 1, fontSize: 13, wordBreak: "break-all" }}>
              {embedded.address}
            </code>
            <button
              type="button"
              onClick={async () => {
                await navigator.clipboard.writeText(embedded.address);
                setCopied(true);
                setTimeout(() => setCopied(false), 1200);
              }}
              style={{
                padding: "6px 12px",
                border: "1px solid #1c1916",
                background: "#fff",
                borderRadius: 3,
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        ) : (
          <p style={{ color: "#6b655c" }}>
            Provisioning your embedded wallet… This usually takes a few seconds
            after sign-in.
          </p>
        )}
      </section>

      <section style={{ marginTop: 32 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>External wallets</h2>
        <p style={{ color: "#6b655c", fontSize: 13, marginTop: 0 }}>
          Link an external Solana wallet (Phantom, Solflare, …) to use it
          alongside your embedded wallet.
        </p>
        <button
          type="button"
          onClick={() => linkWallet()}
          style={{
            padding: "8px 14px",
            border: "1px solid #1c1916",
            background: "#1c1916",
            color: "#fff",
            borderRadius: 3,
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          Link external wallet
        </button>
      </section>
    </main>
  );
}
