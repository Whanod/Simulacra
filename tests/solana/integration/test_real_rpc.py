"""Real-RPC integration tests for Solana ingestion (PRD US-001 lines 240-243).

These tests are skipped by default and only run when archival-RPC access is
configured. They live in a dedicated CI lane: a regular ``pytest`` invocation
will collect them but skip every case unless ``SOLANA_ARCHIVAL_RPC_URL`` is
set in the environment.

Set the env var to a Solana JSON-RPC endpoint with historical block access
(e.g. Helius, Triton One, QuickNode archival) to enable the lane:

    export SOLANA_ARCHIVAL_RPC_URL="https://mainnet.helius-rpc.com/?api-key=..."
    pytest tests/solana/integration/

The expected transaction count is hand-recorded from a one-shot archival pull
(PRD line 242: "N matches a precomputed expected value within tolerance"). It
can be overridden per-environment via ``SOLANA_EXPECTED_TX_COUNT_250M`` for
operators who want to recompute the ground truth without editing the test.

For the program-accounts-at-slot test (PRD line 243), the historical backend
needs ``getProgramAccountsAtSlot``-style support — plain ``getProgramAccounts``
with ``minContextSlot`` is explicitly rejected by PRD line 162. The test
defaults to Triton's ``getProgramAccountsAtSlot`` JSON-RPC method and can be
pointed at an alternative archival endpoint via
``SOLANA_ARCHIVAL_ACCOUNT_RPC_URL`` for providers that gate historical account
state behind a separate URL or API key.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLANA_ARCHIVAL_RPC_URL"),
    reason="SOLANA_ARCHIVAL_RPC_URL not set; archival-RPC integration lane disabled",
)


_KNOWN_SLOT = 250_000_000
_EXPECTED_TX_COUNT_DEFAULT = 1100
_EXPECTED_TX_TOLERANCE = 250

# Orca Whirlpool program ID (mainnet).
_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
# Canonical Orca Whirlpool SOL/USDC pool (tickSpacing=4); the highest-volume
# SOL/USDC venue across the relevant slot range and a stable assertion target
# for "non-empty pool list including the canonical SOL/USDC pool address."
_CANONICAL_SOL_USDC_WHIRLPOOL = "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"


def _expected_tx_count() -> int:
    raw = os.environ.get("SOLANA_EXPECTED_TX_COUNT_250M")
    return int(raw) if raw else _EXPECTED_TX_COUNT_DEFAULT


class _ArchivalRpcClient:
    """Minimal ``SolanaClient`` adapter over the JSON-RPC ``getBlock`` endpoint.

    Implemented with stdlib ``urllib`` so the integration lane does not require
    the ``solana-rpc`` extra to be installed — operators can run this lane
    against any provider that speaks Solana JSON-RPC, including managed ones
    that ship their own Python SDK.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def get_block(self, slot: int) -> dict[str, Any]:
        import json
        import urllib.request

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBlock",
            "params": [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": True,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if "error" in body and body["error"] is not None:
            raise RuntimeError(f"getBlock({slot}) RPC error: {body['error']}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"getBlock({slot}) returned non-dict result: {result!r}")
        result.setdefault("slot", slot)
        return result


class _ArchivalAccountBackend:
    """``HistoricalAccountBackend`` adapter using ``getProgramAccountsAtSlot``.

    Triton One exposes ``getProgramAccountsAtSlot`` as a first-class JSON-RPC
    method that returns true historical account state — the only flavor
    permitted by PRD line 162. Other archival providers offer equivalents
    (Helius archive, Solana indexers); operators on those should set
    ``SOLANA_ARCHIVAL_ACCOUNT_RPC_URL`` to a Triton-compatible endpoint or
    swap in a custom backend by editing this adapter.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]:
        import json
        import urllib.request

        config: dict[str, Any] = {
            "encoding": "base64",
            "commitment": "confirmed",
            "slot": slot,
        }
        if discriminator is not None:
            import base64
            config["filters"] = [
                {
                    "memcmp": {
                        "offset": 0,
                        "bytes": base64.b64encode(discriminator).decode("ascii"),
                        "encoding": "base64",
                    }
                }
            ]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccountsAtSlot",
            "params": [program_id, config],
        }
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if "error" in body and body["error"] is not None:
            raise RuntimeError(
                f"getProgramAccountsAtSlot({program_id}, {slot}) error: "
                f"{body['error']}"
            )
        result = body.get("result")
        accounts: list[dict[str, Any]]
        if isinstance(result, list):
            accounts = [a for a in result if isinstance(a, dict)]
        elif isinstance(result, dict):
            inner = result.get("value") or result.get("accounts") or []
            accounts = [a for a in inner if isinstance(a, dict)]
        else:
            raise RuntimeError(
                f"getProgramAccountsAtSlot({program_id}, {slot}) returned "
                f"unexpected result shape: {type(result).__name__}"
            )
        return {
            "program_id": program_id,
            "slot": slot,
            "accounts": accounts,
        }


def test_get_slot_known_mainnet_slot_returns_expected_tx_count() -> None:
    from defi_sim_solana.replay.slot_client import clear_slot_cache, get_slot

    clear_slot_cache()
    client = _ArchivalRpcClient(os.environ["SOLANA_ARCHIVAL_RPC_URL"])
    snapshot = get_slot(_KNOWN_SLOT, client=client)

    expected = _expected_tx_count()
    actual = len(snapshot.transactions)
    assert abs(actual - expected) <= _EXPECTED_TX_TOLERANCE, (
        f"slot {_KNOWN_SLOT}: tx count {actual} differs from expected "
        f"{expected} by more than tolerance {_EXPECTED_TX_TOLERANCE}"
    )
    assert snapshot.slot == _KNOWN_SLOT
    assert snapshot.transaction_compute_units, (
        "snapshot must populate per-tx compute units from getBlock meta"
    )


def test_get_program_accounts_at_slot_known_program_returns_known_pool() -> None:
    from defi_sim_solana.replay.account_client import (
        clear_program_accounts_cache,
        get_program_accounts_at_slot,
    )

    clear_program_accounts_cache()
    endpoint = os.environ.get(
        "SOLANA_ARCHIVAL_ACCOUNT_RPC_URL",
        os.environ["SOLANA_ARCHIVAL_RPC_URL"],
    )
    backend = _ArchivalAccountBackend(endpoint)
    snapshot = get_program_accounts_at_slot(
        _WHIRLPOOL_PROGRAM,
        _KNOWN_SLOT,
        backend=backend,
    )

    assert snapshot.program_id == _WHIRLPOOL_PROGRAM
    assert snapshot.slot == _KNOWN_SLOT
    assert snapshot.accounts, (
        f"Whirlpool program {_WHIRLPOOL_PROGRAM} returned zero accounts at "
        f"slot {_KNOWN_SLOT}; the historical backend likely does not actually "
        "support as-of-slot reads (PRD line 162) or the slot predates pool "
        "creation."
    )
    pubkeys = {a.pubkey for a in snapshot.accounts}
    assert _CANONICAL_SOL_USDC_WHIRLPOOL in pubkeys, (
        f"canonical SOL/USDC Whirlpool {_CANONICAL_SOL_USDC_WHIRLPOOL} not "
        f"found in {len(pubkeys)} returned accounts at slot {_KNOWN_SLOT}"
    )
