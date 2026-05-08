"""Solana network-bound code: RPC clients, fork hydration, JSON-RPC compat.

Engine code (slot clock, scheduler, fee market, bundle auction) lives in
`defi_sim` and stays chain-shape-agnostic. Distinctively Solana-bound code
(`helius-sdk`, `solders`, Yellowstone gRPC) lives here behind the
`solana-rpc` optional dependency.
"""
