"use client";

import { useState } from "react";
import { usePrivy } from "@privy-io/react-auth";
import { useWallets as useSolanaWallets } from "@privy-io/react-auth/solana";

import Topbar from "@/components/shell/Topbar";

/**
 * Wallets settings page (Privy v1, plan §5.9). Shown only to signed-in
 * users via the UserChip popover; not part of onboarding so the gate
 * stays strictly email-only. Layout uses the studio's CSS vars (panels
 * styled via `.wallets-page*` in globals.css).
 */
export default function WalletsSettingsPage() {
  const { ready, authenticated, linkWallet } = usePrivy();
  const { wallets } = useSolanaWallets();
  const embedded = wallets[0] ?? null;
  const [copied, setCopied] = useState(false);

  if (!ready) {
    return (
      <>
        <Topbar title="Wallets" />
        <div className="wallets-page">
          <p className="wallets-page__placeholder">Loading…</p>
        </div>
      </>
    );
  }

  if (!authenticated) {
    return (
      <>
        <Topbar title="Wallets" />
        <div className="wallets-page">
          <p className="wallets-page__placeholder">Sign in to manage wallets.</p>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title="Wallets" />
      <div className="wallets-page">
        <section className="wallets-page__section">
          <h2>Embedded Solana wallet</h2>
          {embedded ? (
            <div className="wallets-page__panel">
              <code className="wallets-page__addr">{embedded.address}</code>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={async () => {
                  await navigator.clipboard.writeText(embedded.address);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1200);
                }}
              >
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          ) : (
            <p className="wallets-page__placeholder">
              Provisioning your embedded wallet… This usually takes a few
              seconds after sign-in.
            </p>
          )}
        </section>

        <section className="wallets-page__section">
          <h2>External wallets</h2>
          <p className="wallets-page__hint">
            Link an external Solana wallet (Phantom, Solflare, …) to use it
            alongside your embedded wallet.
          </p>
          <button
            type="button"
            className="btn btn-primary cta-primary"
            onClick={() => linkWallet()}
          >
            Link external wallet
          </button>
        </section>
      </div>
    </>
  );
}
