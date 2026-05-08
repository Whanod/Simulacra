"use client";

import { useWallet } from "@solana/wallet-adapter-react";
import type { WalletName } from "@solana/wallet-adapter-base";
import { useMemo } from "react";
import { useToast } from "@/components/feedback/ToastProvider";

function shortAddress(value: string): string {
  if (value.length <= 12) return value;
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

export default function WalletConnectButton() {
  const {
    connect,
    connected,
    connecting,
    disconnect,
    publicKey,
    select,
    wallet,
    wallets,
  } = useWallet();
  const { showToast } = useToast();

  const selectedName = wallet?.adapter.name ?? "";
  const walletOptions = useMemo(
    () =>
      wallets.map((item) => ({
        name: item.adapter.name,
        readyState: String(item.readyState),
      })),
    [wallets],
  );

  async function handleConnect() {
    if (!wallet) {
      showToast("Select an installed wallet first", "error");
      return;
    }
    try {
      await connect();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Wallet connection failed", "error");
    }
  }

  async function handleDisconnect() {
    try {
      await disconnect();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Wallet disconnect failed", "error");
    }
  }

  if (connected && publicKey) {
    return (
      <div className="wallet-connect" data-testid="wallet-connected">
        <span className="wallet-pubkey" title={publicKey.toBase58()}>
          {shortAddress(publicKey.toBase58())}
        </span>
        <button
          className="btn btn-secondary btn-sm"
          data-testid="wallet-disconnect-button"
          type="button"
          onClick={handleDisconnect}
        >
          Disconnect
        </button>
      </div>
    );
  }

  return (
    <div className="wallet-connect" data-testid="wallet-connect-control">
      <select
        aria-label="Wallet"
        className="wallet-select"
        data-testid="wallet-select"
        value={selectedName}
        onChange={(event) => select(event.target.value as WalletName)}
        disabled={connecting || walletOptions.length === 0}
      >
        <option value="">
          {walletOptions.length === 0 ? "No wallet" : "Select wallet"}
        </option>
        {walletOptions.map((option) => (
          <option key={option.name} value={option.name}>
            {option.name}
            {option.readyState === "Installed" ? "" : " (detected)"}
          </option>
        ))}
      </select>
      <button
        className="btn btn-secondary btn-sm"
        data-testid="wallet-connect-button"
        type="button"
        onClick={handleConnect}
        disabled={connecting || !wallet}
      >
        {connecting ? "Connecting..." : "Connect wallet"}
      </button>
    </div>
  );
}
