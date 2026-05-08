"use client";

import { useWallet } from "@solana/wallet-adapter-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  simulationService,
  type WalletArtifactList,
} from "@/lib/services/simulationService";
import { toToastMessage } from "@/lib/api/errors";

function shortId(value: string): string {
  if (value.length <= 12) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function dateLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

export default function WalletArtifactsPanel() {
  const router = useRouter();
  const { connected, publicKey } = useWallet();
  const [data, setData] = useState<WalletArtifactList | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready">("idle");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!connected || !publicKey) {
      setData(null);
      setStatus("idle");
      setError(null);
      return;
    }
    setStatus("loading");
    setError(null);
    try {
      const next = await simulationService.listWalletArtifacts(publicKey.toBase58());
      setData(next);
      setStatus("ready");
    } catch (err) {
      setError(toToastMessage(err));
      setStatus("ready");
    }
  }, [connected, publicKey]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!connected || !publicKey) return;
    const walletPubkey = publicKey.toBase58();
    const handleArtifactsUpdated = (event: Event) => {
      const detail = (event as CustomEvent<{ walletPubkey?: string }>).detail;
      if (!detail?.walletPubkey || detail.walletPubkey === walletPubkey) {
        void refresh();
      }
    };
    window.addEventListener(
      "defi-sim:wallet-artifacts-updated",
      handleArtifactsUpdated,
    );
    return () => {
      window.removeEventListener(
        "defi-sim:wallet-artifacts-updated",
        handleArtifactsUpdated,
      );
    };
  }, [connected, publicKey, refresh]);

  if (!connected || !publicKey) return null;

  return (
    <section className="wallet-panel" data-testid="wallet-artifacts-panel">
      <div className="wallet-panel-header">
        <div>
          <div className="wallet-panel-title">Saved artifacts</div>
          <div className="wallet-panel-owner">{data?.count ?? 0} permanent</div>
        </div>
        <button
          className="btn btn-secondary btn-sm wallet-refresh-action"
          data-testid="wallet-artifacts-refresh-button"
          type="button"
          onClick={() => void refresh()}
          disabled={status === "loading"}
        >
          Refresh
        </button>
      </div>

      {status === "loading" ? (
        <p className="wallet-panel-empty">Loading artifacts...</p>
      ) : null}

      {error ? (
        <p className="wallet-panel-error" data-testid="wallet-artifacts-error">
          {error}
        </p>
      ) : null}

      {data && data.artifacts.length > 0 ? (
        <div className="wallet-artifact-list" data-testid="wallet-artifact-list">
          {data.artifacts.slice(0, 5).map((artifact) => (
            <button
              className="wallet-artifact-row"
              key={artifact.id}
              type="button"
              onClick={() => router.push(`/results/${artifact.id}`)}
              title={artifact.id}
            >
              <div>
                <strong>{artifact.name}</strong>
                <span>
                  {artifact.status} - {dateLabel(artifact.createdAt)}
                </span>
              </div>
              <code>{shortId(artifact.id)}</code>
            </button>
          ))}
        </div>
      ) : status === "ready" && !error ? (
        <p className="wallet-panel-empty">No saved artifacts.</p>
      ) : null}
    </section>
  );
}
