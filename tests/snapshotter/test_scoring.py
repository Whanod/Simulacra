"""Unit tests for ``tools.snapshotter.scoring`` (FIX-019)."""

from __future__ import annotations

from typing import Any

from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_solana.replay.slot_client import SlotSnapshot
from tools.snapshotter import (
    DEFAULT_THRESHOLDS,
    StressCategory,
    extract_signals,
    score_for_category,
)


def _build_snapshot(
    *,
    slot: int = 100,
    transactions: list[dict[str, Any]] | None = None,
    compute_units: list[int] | None = None,
) -> SlotSnapshot:
    """Build a synthetic :class:`SlotSnapshot` from a list of tx dicts."""
    txns = tuple(transactions or ())
    cu = tuple(compute_units or ([0] * len(txns)))
    return SlotSnapshot(
        slot=slot,
        transactions=txns,
        transaction_compute_units=cu,
        raw={"transactions": list(txns)},
    )


def _vote_tx() -> dict[str, Any]:
    """A boring transaction that touches a non-DEX, non-tip program."""
    return {
        "message": {
            "accountKeys": ["Fee1", "VoteProgram1111111"],
            "instructions": [{"programIdIndex": 1, "accounts": [], "data": ""}],
        },
        "meta": {"computeUnitsConsumed": 5000, "innerInstructions": []},
    }


def _tip_tx(lamports: int) -> dict[str, Any]:
    """Synthesize a Jito tip transaction (raw system-program transfer)."""
    import base64

    tip_account = DEFAULT_JITO_TIP_ACCOUNTS[0]
    data = (2).to_bytes(4, "little") + lamports.to_bytes(8, "little")
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "message": {
            "accountKeys": ["Sender", tip_account, "11111111111111111111111111111111"],
            "instructions": [
                {"programIdIndex": 2, "accounts": [0, 1], "data": encoded},
            ],
        },
        "meta": {"computeUnitsConsumed": 200, "innerInstructions": []},
    }


def test_extract_signals_counts_tx_count_and_compute_units() -> None:
    snap = _build_snapshot(
        transactions=[_vote_tx(), _vote_tx(), _vote_tx()],
        compute_units=[1000, 2000, 3000],
    )
    signals = extract_signals(snap)
    assert signals.tx_count == 3
    assert signals.total_compute_units == 6000


def test_extract_signals_counts_jito_tips_via_raw_fallback() -> None:
    """Raw system-program transfers to Jito accounts count as tips.

    The materializer rejects ill-formed tip txs; the raw-fallback counter
    keeps the steady-state guard honest in those cases.
    """
    snap = _build_snapshot(transactions=[_tip_tx(50_000), _tip_tx(150_000)])
    signals = extract_signals(snap)
    assert signals.tip_count >= 2


def test_score_steady_state_qualifies_on_quiet_slot() -> None:
    snap = _build_snapshot(
        transactions=[_vote_tx()] * 800,
        compute_units=[3000] * 800,
    )
    signals = extract_signals(snap)
    score = score_for_category(signals, StressCategory.STEADY_STATE)
    assert score.qualifies is True


def test_score_steady_state_rejects_busy_slot() -> None:
    """Slots over the tx-count cap do not count as steady-state."""
    snap = _build_snapshot(
        transactions=[_vote_tx()] * 5000,
        compute_units=[10000] * 5000,
    )
    signals = extract_signals(snap)
    score = score_for_category(signals, StressCategory.STEADY_STATE)
    assert score.qualifies is False
    assert f"<= {DEFAULT_THRESHOLDS.steady_state_max_tx_count}" in score.reason


def test_score_steady_state_rejects_slot_with_too_many_tips() -> None:
    """A slot above the tip cap is auction-contended, not steady-state."""
    snap = _build_snapshot(transactions=[_tip_tx(100_000)] * 10)
    signals = extract_signals(snap)
    score = score_for_category(signals, StressCategory.STEADY_STATE)
    assert score.qualifies is False
    assert "tip_count" in score.reason
