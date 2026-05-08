"use client";

import { ConnectionProvider, WalletProvider } from "@solana/wallet-adapter-react";
import type { Adapter, WalletError } from "@solana/wallet-adapter-base";
import { clusterApiUrl } from "@solana/web3.js";
import { useMemo, type ReactNode } from "react";

const WALLET_STORAGE_KEY = "defi-sim-wallet-name";

function walletRpcEndpoint(): string {
  return process.env.NEXT_PUBLIC_SOLANA_RPC_URL || clusterApiUrl("devnet");
}

function handleWalletError(error: WalletError, adapter?: Adapter): void {
  console.warn("Wallet adapter error", {
    wallet: adapter?.name,
    message: error.message,
    error,
  });
}

export default function StudioWalletProvider({ children }: { children: ReactNode }) {
  const endpoint = useMemo(walletRpcEndpoint, []);
  const wallets = useMemo<Adapter[]>(() => [], []);

  return (
    <ConnectionProvider endpoint={endpoint} config={{ commitment: "confirmed" }}>
      <WalletProvider
        wallets={wallets}
        autoConnect
        localStorageKey={WALLET_STORAGE_KEY}
        onError={handleWalletError}
      >
        {children}
      </WalletProvider>
    </ConnectionProvider>
  );
}
