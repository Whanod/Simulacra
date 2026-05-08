"use client";

import { useWallet } from "@solana/wallet-adapter-react";
import { useCallback, useEffect, useState } from "react";
import { useToast } from "@/components/feedback/ToastProvider";
import {
  simulationService,
  type RunShareStatus,
} from "@/lib/services/simulationService";
import { toToastMessage } from "@/lib/api/errors";

interface WalletArtifactPersistenceProps {
  runId: string;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function shortAddress(value: string): string {
  if (value.length <= 12) return value;
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function expiryLabel(status: RunShareStatus | null): string {
  if (!status) return "Checking persistence status...";
  if (status.permanent) {
    return status.walletOwner
      ? `Permanent artifact owned by ${shortAddress(status.walletOwner)}.`
      : "Permanent artifact.";
  }
  if (!status.expiresAt) return "Ephemeral artifact.";
  return `Ephemeral link expires ${new Date(status.expiresAt).toLocaleString()}.`;
}

export default function WalletArtifactPersistence({
  runId,
}: WalletArtifactPersistenceProps) {
  const { connected, publicKey, signMessage } = useWallet();
  const { showToast } = useToast();
  const [status, setStatus] = useState<RunShareStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await simulationService.getRunShareStatus(runId);
      setStatus(next);
    } catch (err) {
      setError(toToastMessage(err));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handlePersist = useCallback(async () => {
    if (!connected || !publicKey) {
      showToast("Connect a wallet to save permanently", "error");
      return;
    }
    if (!signMessage) {
      showToast("Selected wallet cannot sign messages", "error");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const walletPubkey = publicKey.toBase58();
      const challenge = await simulationService.createWalletPersistenceChallenge(
        runId,
        walletPubkey,
      );
      const messageBytes = new TextEncoder().encode(challenge.message);
      const signatureBytes = await signMessage(messageBytes);
      const promoted = await simulationService.promoteWalletArtifact(runId, {
        walletPubkey,
        nonce: challenge.nonce,
        signature: bytesToBase64(signatureBytes),
        encoding: "base64",
      });
      setStatus(promoted);
      window.dispatchEvent(
        new CustomEvent("defi-sim:wallet-artifacts-updated", {
          detail: { walletPubkey },
        }),
      );
      showToast("Artifact saved permanently", "success");
    } catch (err) {
      const message = toToastMessage(err);
      setError(message);
      showToast(`Save failed: ${message}`, "error");
    } finally {
      setSaving(false);
    }
  }, [connected, publicKey, runId, showToast, signMessage]);

  const permanent = status?.permanent === true;
  return (
    <div className="wallet-artifact-persistence" data-testid="wallet-artifact-persistence">
      <div>
        <strong>Artifact persistence</strong>
        <p>{error ?? expiryLabel(status)}</p>
      </div>
      <button
        className="btn btn-secondary btn-sm"
        data-testid="wallet-persist-button"
        type="button"
        onClick={handlePersist}
        disabled={loading || saving || permanent || !connected || !publicKey}
      >
        {permanent ? "Saved" : saving ? "Saving..." : "Save permanently"}
      </button>
    </div>
  );
}
