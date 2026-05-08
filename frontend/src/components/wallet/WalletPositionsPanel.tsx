"use client";

import { useWallet } from "@solana/wallet-adapter-react";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { useWalletPositions } from "@/components/wallet/useWalletPositions";

function shortAddress(value: string): string {
  if (value.length <= 12) return value;
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

export default function WalletPositionsPanel() {
  const router = useRouter();
  const { connected, publicKey } = useWallet();
  const { data, error, refresh, status } = useWalletPositions();

  const lpCandidateCount = useMemo(
    () =>
      data?.positions.filter((position) => position.kind === "lp_position_candidate")
        .length ?? 0,
    [data],
  );

  if (!connected || !publicKey) {
    return null;
  }

  return (
    <section className="wallet-panel" data-testid="wallet-positions-panel">
      <div className="wallet-panel-header">
        <div>
          <div className="wallet-panel-title">Wallet positions</div>
          <div className="wallet-panel-owner" title={publicKey.toBase58()}>
            {shortAddress(publicKey.toBase58())}
          </div>
        </div>
        <button
          className="btn btn-secondary btn-sm wallet-refresh-action"
          data-testid="wallet-refresh-button"
          type="button"
          onClick={() => void refresh()}
          title="Refresh wallet accounts"
          disabled={status === "loading"}
        >
          Refresh
        </button>
      </div>

      <div className="wallet-stats" data-testid="wallet-position-summary">
        <span>{data?.accountsScanned ?? 0} accounts</span>
        <span>{lpCandidateCount} LP candidates</span>
      </div>

      {status === "loading" ? (
        <p className="wallet-panel-empty">Loading accounts...</p>
      ) : null}

      {error ? (
        <p className="wallet-panel-error" data-testid="wallet-position-error">
          {error}
        </p>
      ) : null}

      {data && data.positions.length > 0 ? (
        <div className="wallet-position-list" data-testid="wallet-position-list">
          {data.positions.slice(0, 6).map((position) => (
            <div className="wallet-position-row" key={position.id}>
              <div>
                <strong>{position.protocol}</strong>
                <span>{position.label}</span>
              </div>
              <code title={position.mint ?? position.account}>{position.amount}</code>
            </div>
          ))}
        </div>
      ) : status !== "loading" ? (
        <p className="wallet-panel-empty">No token accounts found.</p>
      ) : null}

      <button
        className="btn btn-secondary btn-sm wallet-fork-link"
        data-testid="wallet-open-fork-button"
        type="button"
        onClick={() => router.push("/bundle-simulator")}
      >
        Fork with my positions
      </button>
    </section>
  );
}
