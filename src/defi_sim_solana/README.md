# `defi_sim_solana` — Solana network-bound code

This package contains Solana-specific code that depends on archival RPC
clients and Solana-native parsing libraries. It is intentionally separated
from `defi_sim` so the engine itself stays chain-shape-agnostic.

## Optional dependency: `solana-rpc`

Install with:

```bash
pip install -e ".[solana-rpc]"
```

### Pinned clients

| Package          | Purpose                                            | Historical state support                                                                 |
| ---------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `helius-sdk`     | Default archival RPC client (paid plan)            | Yes — Helius offers archival `getBlock` and an `Enhanced Transactions API` for old slots |
| `solders`        | Pubkey/signature/transaction parsing               | n/a (parsing only)                                                                       |
| `solana`         | `solana-py` for SPL-token / account-data helpers   | n/a (parsing/utility)                                                                    |
| `grpcio`         | Yellowstone Geyser gRPC streams (when configured)  | Yes — Yellowstone archive streams expose historical slots if the provider retains them   |

All four are actively maintained as of 2026; `helius-sdk` and `solders` are
the load-bearing pieces. `solana-py` and `grpcio` are kept narrow — we use
them for typed account parsing and for optional gRPC archive streams; we do
not use `solana-py`'s RPC client (its async API is moving and Helius's SDK
gives us the historical-state path we need).

### Historical account-state backend (Phase 2 entry gate)

Plain `getProgramAccounts(program_id, minContextSlot=slot)` is **not** an
as-of-slot query — it only ensures the RPC node has reached at least that
slot. The Phase 2 entry gate proves that the chosen backend (Helius
archival or a Yellowstone-fed indexer) returns true historical account
state; until that gate is signed off, `account_client.get_program_accounts_at_slot`
will refuse to fall back to the misuse path.
