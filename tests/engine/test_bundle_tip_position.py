"""Bundle tip-position semantics tests (PRD US-011 line 867).

The bundle is atomic: any revert undoes all state mutations from positions
``0..j`` and skips every tip recipient credit, regardless of where the
``TipPayment`` lives in the bundle. These tests pin down that "tip first
vs. tip last" yields the *same* (no-tip-paid) outcome under any failure
shape, so the searcher's tip placement is a decision about CU/ordering,
not revenue.

The unit tests validate the per-bundle ``paid_tip_payments`` helper.
Integration tests at PRD lines 916-919 drive the bundle through the
engine's atomic-execution primitive (``SimulationEngine._execute_bundle_atomically``)
to confirm the tip-position rule holds end-to-end against real market
state mutations.
"""

from __future__ import annotations

import copy

from defi_sim.core.types import SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import (
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
    TipPayment,
)
from defi_sim.engine.transactions import VersionedTransaction


_SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "searcher",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "seed": 11,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def _vtx() -> VersionedTransaction:
    return VersionedTransaction(
        actions=[SwapAction(agent_id="a", token_in="SOL", token_out="USDC", amount_in=1)]
    )


def _bundle_with_tip_at(tx_index: int, *, num_txs: int = 4) -> Bundle:
    return Bundle(
        txs=[_vtx() for _ in range(num_txs)],
        tip_payments=[
            TipPayment(
                tx_index=tx_index,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="tip-acct-1",
            )
        ],
    )


def test_tip_first_with_later_tx_failure_pays_no_tip() -> None:
    """Tip at position 0; revert at position 3 → no tip paid."""
    bundle = _bundle_with_tip_at(tx_index=0)
    paid = bundle.paid_tip_payments(reverted=True, failed_at_index=3)
    assert paid == []


def test_tip_last_with_earlier_tx_failure_pays_no_tip() -> None:
    """Tip at last position; revert at position 0 → tip never reached, no tip paid."""
    bundle = _bundle_with_tip_at(tx_index=3)
    paid = bundle.paid_tip_payments(reverted=True, failed_at_index=0)
    assert paid == []


def test_tip_at_failing_position_pays_no_tip() -> None:
    """Tip-tx itself reverts → no tip paid."""
    bundle = _bundle_with_tip_at(tx_index=2)
    paid = bundle.paid_tip_payments(reverted=True, failed_at_index=2)
    assert paid == []


def test_all_success_pays_all_tips() -> None:
    """Bundle fully succeeds → all declared tips credit the recipient."""
    bundle = _bundle_with_tip_at(tx_index=0)
    paid = bundle.paid_tip_payments(reverted=False, failed_at_index=None)
    assert len(paid) == 1
    assert paid[0].tx_index == 0
    assert paid[0].lamports == MIN_BUNDLE_TIP_LAMPORTS


def test_all_success_pays_multi_position_tips() -> None:
    """Tips at positions 0 and 3 both credit on full success."""
    bundle = Bundle(
        txs=[_vtx() for _ in range(4)],
        tip_payments=[
            TipPayment(tx_index=0, location="instruction", lamports=600, recipient="t1"),
            TipPayment(tx_index=3, location="standalone_tx", lamports=400, recipient="t2"),
        ],
    )
    paid = bundle.paid_tip_payments(reverted=False, failed_at_index=None)
    assert len(paid) == 2
    assert {tp.recipient for tp in paid} == {"t1", "t2"}
    assert sum(tp.lamports for tp in paid) == bundle.tip_lamports


def test_revert_kills_tips_at_every_position() -> None:
    """Tips at positions 0 AND 3 both fail to credit on any-tx revert."""
    bundle = Bundle(
        txs=[_vtx() for _ in range(4)],
        tip_payments=[
            TipPayment(tx_index=0, location="instruction", lamports=600, recipient="t1"),
            TipPayment(tx_index=3, location="standalone_tx", lamports=400, recipient="t2"),
        ],
    )
    # Revert at position 2 — between the two tips. Atomic semantics kill both.
    paid = bundle.paid_tip_payments(reverted=True, failed_at_index=2)
    assert paid == []


def test_returned_list_is_a_copy_not_alias() -> None:
    """``paid_tip_payments`` must not alias the bundle's internal list."""
    bundle = _bundle_with_tip_at(tx_index=0)
    paid = bundle.paid_tip_payments(reverted=False)
    paid.append(
        TipPayment(tx_index=0, location="instruction", lamports=1, recipient="injected")
    )
    assert len(bundle.tip_payments) == 1
    assert bundle.tip_payments[0].recipient == "tip-acct-1"


# --- integration tests (PRD lines 916-919) ----------------------------------


def test_tip_first_with_later_tx_failure_reverts_tip() -> None:
    """PRD line 916: tip-tx at position 0, position 2 reverts -> validator
    does NOT receive tip; bundle reverts atomically."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_volume = searcher.state.cumulative_volume

    # 3-tx bundle: tip-tx at position 0, regular swap at position 1,
    # failing swap at position 2 (token_in does not exist).
    swap_tip = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=1_000)
    swap_mid = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=2_000)
    swap_fail = SwapAction(
        agent_id="searcher", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1
    )
    bundle = Bundle(
        txs=[
            VersionedTransaction(actions=[swap_tip]),
            VersionedTransaction(actions=[swap_mid]),
            VersionedTransaction(actions=[swap_fail]),
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="validator-tip-acct",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    # Bundle reverts at the failing position.
    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 2
    assert outcome["executed"] == []

    # State from the first two (successful) txs is rolled back.
    assert searcher.state.balances.get("SOL", 0) == pre_sol
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume == pre_volume

    # Validator does NOT receive the tip even though the tip-tx itself
    # (position 0) executed successfully — atomic rollback kills tips
    # at every position regardless of placement.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert paid == []


def test_tip_last_with_earlier_tx_failure_does_not_pay_tip() -> None:
    """PRD line 917: tip-tx at position 2 (last), position 0 reverts ->
    bundle never reaches tip-tx, validator does NOT receive tip."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_volume = searcher.state.cumulative_volume

    # 3-tx bundle: failing swap at position 0, regular swap at position 1,
    # tip-tx at position 2 (last). Bundle reverts before tip-tx executes.
    swap_fail = SwapAction(
        agent_id="searcher", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1
    )
    swap_mid = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=2_000)
    swap_tip = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=1_000)
    bundle = Bundle(
        txs=[
            VersionedTransaction(actions=[swap_fail]),
            VersionedTransaction(actions=[swap_mid]),
            VersionedTransaction(actions=[swap_tip]),
        ],
        tip_payments=[
            TipPayment(
                tx_index=2,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="validator-tip-acct",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    # Bundle reverts at position 0 — tip-tx at position 2 never executes.
    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 0
    assert outcome["executed"] == []

    # No state mutations — failing tx was at position 0.
    assert searcher.state.balances.get("SOL", 0) == pre_sol
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume == pre_volume

    # Tip-tx never reached, so validator does NOT receive the tip.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert paid == []


def test_tip_first_with_all_txs_success_pays_tip() -> None:
    """PRD line 918: control case — tip-tx at position 0, all txs succeed,
    validator DOES receive the tip."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_volume = searcher.state.cumulative_volume

    # 3-tx bundle: tip-tx at position 0, regular swaps at positions 1 & 2.
    # All swaps are valid (USDC -> SOL with positive amount on a funded agent).
    swap_tip = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=1_000)
    swap_mid = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=2_000)
    swap_end = SwapAction(agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=3_000)
    bundle = Bundle(
        txs=[
            VersionedTransaction(actions=[swap_tip]),
            VersionedTransaction(actions=[swap_mid]),
            VersionedTransaction(actions=[swap_end]),
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="validator-tip-acct",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    # Whole bundle commits.
    assert outcome["reverted"] is False
    assert outcome["failed_at_index"] is None
    assert outcome["failed_reason"] is None
    assert len(outcome["executed"]) == 3

    # State reflects all three swaps.
    assert searcher.state.balances.get("SOL", 0) > pre_sol
    assert engine._market._reserves != pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume > pre_volume

    # Validator receives the tip.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert len(paid) == 1
    assert paid[0].tx_index == 0
    assert paid[0].lamports == MIN_BUNDLE_TIP_LAMPORTS
    assert paid[0].recipient == "validator-tip-acct"


def test_partial_failure_in_middle_position_reverts_subsequent_state() -> None:
    """PRD line 919: 3-tx bundle, position 0 succeeds, position 1 fails,
    position 2 never executes. Full state-revert assertion: position 0's
    committed mutations are rolled back, and the bundle's tip is not paid."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    searcher = next(a for a in engine._agents if a.agent_id == "searcher")
    pre_sol = searcher.state.balances.get("SOL", 0)
    pre_usdc = searcher.state.balances.get("USDC", 0)
    pre_volume = searcher.state.cumulative_volume

    # 3-tx bundle: succeeding swap at position 0 (would mutate state),
    # failing swap at position 1 (forces rollback), would-succeed swap at
    # position 2 (never executes).
    swap_first = SwapAction(
        agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=2_500
    )
    swap_fail = SwapAction(
        agent_id="searcher", token_in="DOES_NOT_EXIST", token_out="SOL", amount_in=1
    )
    swap_after = SwapAction(
        agent_id="searcher", token_in="USDC", token_out="SOL", amount_in=4_000
    )
    bundle = Bundle(
        txs=[
            VersionedTransaction(actions=[swap_first]),
            VersionedTransaction(actions=[swap_fail]),
            VersionedTransaction(actions=[swap_after]),
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="validator-tip-acct",
            )
        ],
    )

    actions = [a for tx in bundle.txs for a in tx.actions]
    outcome = engine._execute_bundle_atomically(actions, round_num=0, ts=0)

    # Bundle reverts at the failing middle position.
    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 1
    assert outcome["executed"] == []

    # Full state-revert: position 0's successful swap is rolled back.
    assert searcher.state.balances.get("SOL", 0) == pre_sol
    assert searcher.state.balances.get("USDC", 0) == pre_usdc
    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert searcher.state.cumulative_volume == pre_volume

    # Tip is not paid: bundle reverted atomically.
    paid = bundle.paid_tip_payments(
        reverted=outcome["reverted"], failed_at_index=outcome["failed_at_index"]
    )
    assert paid == []
