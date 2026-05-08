"use client";

import { useConnection, useWallet } from "@solana/wallet-adapter-react";
import { useCallback, useEffect, useState } from "react";
import {
  fetchWalletReadOnlyState,
  type WalletReadOnlyState,
} from "@/lib/wallet/positions";

export type WalletPositionStatus = "idle" | "loading" | "ready" | "error";

interface WalletPositionsState {
  status: WalletPositionStatus;
  data: WalletReadOnlyState | null;
  error: string | null;
  refresh: () => Promise<void>;
}

function messageFromError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useWalletPositions(): WalletPositionsState {
  const { connection } = useConnection();
  const { connected, publicKey } = useWallet();
  const [status, setStatus] = useState<WalletPositionStatus>("idle");
  const [data, setData] = useState<WalletReadOnlyState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!connected || !publicKey) {
      setStatus("idle");
      setData(null);
      setError(null);
      return;
    }

    setStatus("loading");
    setError(null);
    try {
      const next = await fetchWalletReadOnlyState(connection, publicKey);
      setData(next);
      setStatus(next.errors.length > 0 ? "error" : "ready");
      setError(next.errors.length > 0 ? next.errors.join("; ") : null);
    } catch (err) {
      setData(null);
      setStatus("error");
      setError(messageFromError(err));
    }
  }, [connected, connection, publicKey]);

  useEffect(() => {
    let ignore = false;
    async function run() {
      if (!connected || !publicKey) {
        setStatus("idle");
        setData(null);
        setError(null);
        return;
      }
      setStatus("loading");
      setError(null);
      try {
        const next = await fetchWalletReadOnlyState(connection, publicKey);
        if (ignore) return;
        setData(next);
        setStatus(next.errors.length > 0 ? "error" : "ready");
        setError(next.errors.length > 0 ? next.errors.join("; ") : null);
      } catch (err) {
        if (ignore) return;
        setData(null);
        setStatus("error");
        setError(messageFromError(err));
      }
    }
    void run();
    return () => {
      ignore = true;
    };
  }, [connected, connection, publicKey]);

  return { status, data, error, refresh };
}
