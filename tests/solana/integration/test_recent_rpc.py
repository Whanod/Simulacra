"""Recent Solana RPC integration tests.

These tests are skipped unless ``SOLANA_RPC_URL`` is set. They intentionally
exercise the normal recent-slot development endpoint, not the archival account
state lane used by calibration.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import pytest

from defi_sim_solana.replay.account_client import (
    JsonRpcRecentAccountBackend,
    clear_program_accounts_cache,
    get_program_accounts_at_slot,
)
from defi_sim_solana.replay.slot_client import clear_slot_cache, get_slot
from defi_sim_solana.replay.whirlpool_hydrator import WHIRLPOOL_POOL_DISCRIMINATOR

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLANA_RPC_URL"),
    reason="SOLANA_RPC_URL not set; recent-RPC integration lane disabled",
)


def _rpc_call(endpoint: str, method: str, params: list[Any] | None = None) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise RuntimeError(f"{method} RPC error: {body['error']!r}")
    return body.get("result")


def test_default_client_get_slot_uses_solana_rpc_url_for_recent_slot() -> None:
    endpoint = os.environ["SOLANA_RPC_URL"]
    current_slot = _rpc_call(endpoint, "getSlot", [{"commitment": "confirmed"}])
    assert isinstance(current_slot, int)

    offset = int(os.environ.get("SOLANA_RECENT_RPC_SLOT_OFFSET", "128"))
    candidate = max(1, current_slot - offset)
    errors: list[str] = []
    clear_slot_cache()

    for slot in range(candidate, max(candidate - 64, 0), -4):
        try:
            snapshot = get_slot(slot, corpus_loader=lambda *_a, **_kw: None)
        except RuntimeError as exc:
            errors.append(f"{slot}: {exc}")
            continue
        assert snapshot.slot == slot
        assert snapshot.raw
        return

    pytest.fail(
        "SOLANA_RPC_URL was reachable via getSlot, but recent getBlock failed "
        f"for all candidate slots near {candidate}: {errors[-3:]}"
    )


WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
# Canonical SOL/USDC Orca Whirlpool, used as a smoke-check that latest-state
# getProgramAccounts returns the expected high-volume pool.
SOL_USDC_WHIRLPOOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"


def test_get_program_accounts_latest_returns_known_pool() -> None:
    """PRD line 245: a latest-state Whirlpool query must include the canonical
    SOL/USDC pool address. Confirms the wrapper, the JSON-RPC backend, and the
    slot==latest enforcement all line up against live RPC.

    Solana advances ~one slot every 400ms, so the snapshot of "latest" can
    drift between the test reading it and the wrapper re-reading it. Retry a
    handful of times to absorb that race; persistent failures still surface.
    """
    endpoint = os.environ["SOLANA_RPC_URL"]
    timeout = float(os.environ.get("SOLANA_RPC_TIMEOUT", "60"))
    backend = JsonRpcRecentAccountBackend(endpoint, timeout=timeout)

    last_error: RuntimeError | None = None
    for _ in range(6):
        clear_program_accounts_cache()
        latest = backend.get_latest_slot()
        try:
            snapshot = get_program_accounts_at_slot(
                WHIRLPOOL_PROGRAM,
                latest,
                backend=backend,
                corpus_loader=lambda *_a, **_kw: None,
                discriminator=WHIRLPOOL_POOL_DISCRIMINATOR,
            )
        except RuntimeError as exc:
            if "neither in the committed corpus" in str(exc):
                last_error = exc
                continue
            raise
        break
    else:
        raise AssertionError(
            f"slot kept advancing during the test (last error: {last_error})"
        )

    assert snapshot.program_id == WHIRLPOOL_PROGRAM
    assert snapshot.accounts, "latest Whirlpool getProgramAccounts must be non-empty"
    pubkeys = {record.pubkey for record in snapshot.accounts}
    assert SOL_USDC_WHIRLPOOL in pubkeys, (
        f"expected canonical SOL/USDC Whirlpool {SOL_USDC_WHIRLPOOL} in "
        f"latest-state response (got {len(pubkeys)} accounts)"
    )
