"""Tests for the ``Validator`` agent (PRD US-012 line 980)."""

from __future__ import annotations

import copy

from defi_sim.agents.validator import Validator, ValidatorParams
from defi_sim.core.types import SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import (
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
    TipPayment,
)
from defi_sim.engine.bundle_auction import BundleAuction
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.leader_schedule import LeaderSchedule, ValidatorStake
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
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


def _bundle_with_tip(tip_lamports: int) -> Bundle:
    action = SwapAction(
        agent_id="searcher",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
    )
    payment = TipPayment(
        tx_index=0,
        location="standalone_tx",
        lamports=tip_lamports,
        recipient="tip-acct",
    )
    return Bundle(
        txs=[VersionedTransaction(actions=[action])],
        tip_payments=[payment],
    )


def _stub_bundle_executor(bundle: Bundle, slot: int) -> BundleExecutionResult:
    del slot
    return BundleExecutionResult(
        reverted=False,
        failed_at_index=None,
        failed_reason=None,
        executed=[
            ExecutedAction(
                action=tx.actions[0],
                execution_cost=0,
                cost_token=None,
                succeeded=True,
            )
            for tx in bundle.txs
        ],
    )


def test_jito_solana_validator_captures_tip_minus_share() -> None:
    """PRD line 981: jito_solana validator gets tip * (1 - stake_pool_share),
    stake-pool address gets tip * stake_pool_share, and the per-epoch
    accumulator records both."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS  # 100_000_000 lamports
    execution.submit_bundle(_bundle_with_tip(tip))

    ctx = SlotContext(
        slot=1,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=lambda action, slot: ExecutedAction(
            action=action, execution_cost=0, cost_token=None, succeeded=True
        ),
        emit=lambda event: None,
        execute_bundle=_stub_bundle_executor,
    )
    execution.execute_slot(ctx)
    engine._credit_validator_revenue(slot=1)

    expected_pool = int(round(tip * 0.05))
    expected_validator = tip - expected_pool

    assert validator.state.balances["SOL"] == expected_validator

    revenue = engine.validator_revenue_by_epoch
    assert 0 in revenue
    assert "val-pk-1" in revenue[0]
    entry = revenue[0]["val-pk-1"]
    assert entry.validator_revenue_lamports == expected_validator
    assert entry.stake_pool_revenue_lamports == expected_pool
    assert entry.client == "jito_solana"


def test_vanilla_validator_captures_no_tip() -> None:
    """PRD line 982: a vanilla-client validator captures no MEV tip revenue
    even when a bundle with a non-zero tip lands in its leader slot.
    Regular block rewards are not the focus here."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    validator = Validator(
        "validator-vanilla",
        ValidatorParams(
            pubkey="val-pk-vanilla",
            client="vanilla",
            stake_pool_share=0.05,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    pre_balance = validator.state.balances.get("SOL", 0)

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS
    execution.submit_bundle(_bundle_with_tip(tip))

    ctx = SlotContext(
        slot=1,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=lambda action, slot: ExecutedAction(
            action=action, execution_cost=0, cost_token=None, succeeded=True
        ),
        emit=lambda event: None,
        execute_bundle=_stub_bundle_executor,
    )
    execution.execute_slot(ctx)
    engine._credit_validator_revenue(slot=1)

    assert validator.state.balances.get("SOL", 0) == pre_balance
    assert engine.validator_revenue_by_epoch == {}


def test_revenue_share_routes_to_stake_pool_address() -> None:
    """PRD line 983: when ``stake_pool_address`` is configured, the
    stake-pool share of every landed tip is credited to that agent's
    SOL balance (in addition to the per-epoch accumulator)."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    pool_agent = engine._find_agent("searcher")
    assert pool_agent is not None
    pool_pre_balance = pool_agent.state.balances.get("SOL", 0)

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_pool_address="searcher",
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS
    execution.submit_bundle(_bundle_with_tip(tip))

    ctx = SlotContext(
        slot=1,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=lambda action, slot: ExecutedAction(
            action=action, execution_cost=0, cost_token=None, succeeded=True
        ),
        emit=lambda event: None,
        execute_bundle=_stub_bundle_executor,
    )
    execution.execute_slot(ctx)
    engine._credit_validator_revenue(slot=1)

    expected_pool = int(round(tip * 0.05))
    expected_validator = tip - expected_pool

    assert pool_agent.state.balances["SOL"] == pool_pre_balance + expected_pool
    assert validator.state.balances["SOL"] == expected_validator

    entry = engine.validator_revenue_by_epoch[0]["val-pk-1"]
    assert entry.validator_revenue_lamports == expected_validator
    assert entry.stake_pool_revenue_lamports == expected_pool


def test_validator_metrics_aggregated_per_epoch() -> None:
    """PRD line 984: per-validator MEV revenue is bucketed into distinct
    epoch entries when leader slots span an epoch boundary, with each
    bucket aggregating the tips landed across all of that epoch's slots."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    # epoch_length_slots=2 -> slots 0,1 in epoch 0; slot 2 in epoch 1.
    execution._leader_schedule = LeaderSchedule(
        validators=[ValidatorStake(pubkey="val-pk-1", stake_lamports=1_000_000_000)],
        epoch_length_slots=2,
    )

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS

    def _drive_slot(slot: int) -> None:
        execution.submit_bundle(_bundle_with_tip(tip))
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=lambda action, slot: ExecutedAction(
                action=action, execution_cost=0, cost_token=None, succeeded=True
            ),
            emit=lambda event: None,
            execute_bundle=_stub_bundle_executor,
        )
        execution.execute_slot(ctx)
        engine._credit_validator_revenue(slot=slot)

    _drive_slot(0)
    _drive_slot(1)
    _drive_slot(2)

    expected_pool = int(round(tip * 0.05))
    expected_validator = tip - expected_pool

    revenue = engine.validator_revenue_by_epoch
    assert set(revenue.keys()) == {0, 1}

    epoch0 = revenue[0]["val-pk-1"]
    assert epoch0.epoch == 0
    assert epoch0.client == "jito_solana"
    assert epoch0.validator_revenue_lamports == 2 * expected_validator
    assert epoch0.stake_pool_revenue_lamports == 2 * expected_pool

    epoch1 = revenue[1]["val-pk-1"]
    assert epoch1.epoch == 1
    assert epoch1.validator_revenue_lamports == expected_validator
    assert epoch1.stake_pool_revenue_lamports == expected_pool


def test_validation_1000_slots_100_sol_splits_95_5() -> None:
    """PRD line 977: one Jito-Solana validator at 100% stake with
    ``stake_pool_share=0.05`` over 1000 slots with bundle tips totaling
    100 SOL credits the validator with 95 SOL and the stake-pool address
    with 5 SOL."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    pool_agent = engine._find_agent("searcher")
    assert pool_agent is not None
    pool_pre_balance = pool_agent.state.balances.get("SOL", 0)

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_pool_address="searcher",
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    num_slots = 1000
    total_tip_lamports = 100 * 1_000_000_000  # 100 SOL
    per_slot_tip = total_tip_lamports // num_slots  # 100_000_000 lamports = 0.1 SOL

    for slot in range(num_slots):
        execution.submit_bundle(_bundle_with_tip(per_slot_tip))
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=lambda action, slot: ExecutedAction(
                action=action, execution_cost=0, cost_token=None, succeeded=True
            ),
            emit=lambda event: None,
            execute_bundle=_stub_bundle_executor,
        )
        execution.execute_slot(ctx)
        engine._credit_validator_revenue(slot=slot)

    expected_pool_total = num_slots * int(round(per_slot_tip * 0.05))  # 5 SOL
    expected_validator_total = num_slots * (per_slot_tip - int(round(per_slot_tip * 0.05)))  # 95 SOL

    assert expected_validator_total == 95 * 1_000_000_000
    assert expected_pool_total == 5 * 1_000_000_000

    assert validator.state.balances["SOL"] == expected_validator_total
    assert pool_agent.state.balances["SOL"] == pool_pre_balance + expected_pool_total


def test_validation_vanilla_validator_no_tip_over_1000_slots() -> None:
    """PRD line 978: a vanilla-client validator captures no tip revenue
    (regular block rewards only) even when bundles with non-zero tips
    land in its leader slots across a long run."""
    engine = build_engine(copy.deepcopy(_SOLANA_SPEC))

    auction = BundleAuction()
    execution = SolanaLikeExecution(bundle_auction=auction)
    engine._execution_model = execution

    validator = Validator(
        "validator-vanilla",
        ValidatorParams(
            pubkey="val-pk-vanilla",
            client="vanilla",
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    pre_balance = validator.state.balances.get("SOL", 0)

    num_slots = 1000
    total_tip_lamports = 100 * 1_000_000_000  # 100 SOL
    per_slot_tip = total_tip_lamports // num_slots  # 0.1 SOL per slot

    for slot in range(num_slots):
        execution.submit_bundle(_bundle_with_tip(per_slot_tip))
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=lambda action, slot: ExecutedAction(
                action=action, execution_cost=0, cost_token=None, succeeded=True
            ),
            emit=lambda event: None,
            execute_bundle=_stub_bundle_executor,
        )
        execution.execute_slot(ctx)
        engine._credit_validator_revenue(slot=slot)

    assert validator.state.balances.get("SOL", 0) == pre_balance
    assert engine.validator_revenue_by_epoch == {}
