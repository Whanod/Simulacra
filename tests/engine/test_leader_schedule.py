"""LeaderSchedule unit tests (PRD US-001).

Locks the stake-weighted leader schedule to its spec: deterministic given
a seed, observed leader share matches configured stake within ±1%, single
validator gets every slot, zero-stake validators are never selected, and
``next_leader_slot`` returns the correct upcoming assignment.
"""

from __future__ import annotations

from collections import Counter

import pytest

from defi_sim.core.clock import SolanaSlotClock
from defi_sim.engine.api import build_engine
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.leader_schedule import LeaderSchedule, ValidatorStake


def test_leader_schedule_deterministic_with_seed() -> None:
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=70),
        ValidatorStake(pubkey="B", stake_lamports=30),
    ]
    schedule_a = LeaderSchedule(validators, seed=42)
    schedule_b = LeaderSchedule(validators, seed=42)
    for slot in range(1000):
        assert schedule_a.leader_for_slot(slot) == schedule_b.leader_for_slot(slot)


def test_leader_schedule_respects_stake_distribution() -> None:
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=70),
        ValidatorStake(pubkey="B", stake_lamports=30),
    ]
    # Use a small epoch_length to keep the test fast while still drawing 100k samples
    # across multiple epochs.
    schedule = LeaderSchedule(validators, seed=7, epoch_length_slots=10_000)
    counts: Counter[str] = Counter()
    n = 100_000
    for slot in range(n):
        counts[schedule.leader_for_slot(slot)] += 1
    share_a = counts["A"] / n
    share_b = counts["B"] / n
    assert abs(share_a - 0.70) < 0.01
    assert abs(share_b - 0.30) < 0.01


def test_leader_schedule_handles_single_validator() -> None:
    schedule = LeaderSchedule(
        [ValidatorStake(pubkey="ONLY", stake_lamports=100)],
        seed=0,
        epoch_length_slots=1000,
    )
    for slot in range(2500):
        assert schedule.leader_for_slot(slot) == "ONLY"


def test_leader_schedule_zero_stake_skipped() -> None:
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=100),
        ValidatorStake(pubkey="Z", stake_lamports=0),
    ]
    schedule = LeaderSchedule(validators, seed=0, epoch_length_slots=1000)
    for slot in range(2000):
        assert schedule.leader_for_slot(slot) != "Z"


def test_next_leader_slot_returns_correct_assignment() -> None:
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=70),
        ValidatorStake(pubkey="B", stake_lamports=30),
    ]
    schedule = LeaderSchedule(validators, seed=11, epoch_length_slots=10_000)
    # Pick a known assignment from the schedule and verify next_leader_slot finds it.
    target_slot = next(
        slot for slot in range(1000) if schedule.leader_for_slot(slot) == "B"
    )
    assert schedule.next_leader_slot("B", after_slot=target_slot - 1) == target_slot


def test_current_leader_deterministic_across_runs_with_same_seed() -> None:
    """PRD US-001 validation: ``current_leader(slot)`` on the execution model
    must be deterministic given the same seed across runs.
    """
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=70),
        ValidatorStake(pubkey="B", stake_lamports=30),
    ]
    exec_a = SolanaLikeExecution(
        leader_schedule=LeaderSchedule(validators, seed=1234, epoch_length_slots=10_000)
    )
    exec_b = SolanaLikeExecution(
        leader_schedule=LeaderSchedule(validators, seed=1234, epoch_length_slots=10_000)
    )
    for slot in range(1000):
        assert exec_a.current_leader(slot) == exec_b.current_leader(slot)


def test_builder_solana_template_default_produces_runnable_spec_with_slot_clock_and_single_validator_schedule() -> None:
    """PRD US-001 validation (line 79):

    The builder's Solana template default — solana_slot clock + solana_like
    execution, no explicit leader_schedule — produces a runnable spec where
    the engine has a SolanaSlotClock and a 1-validator LeaderSchedule.
    """
    spec = {
        "market": {
            "type": "cfamm",
            "tokens": [
                {"id": "SOL", "symbol": "SOL", "decimals": 9},
                {"id": "USDC", "symbol": "USDC", "decimals": 6},
            ],
            "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
            "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
        },
        "agents": [
            {
                "type": "noise",
                "agent_id": "noise-1",
                "params": {"collateral": "USDC", "frequency": 0},
                "initial_balances": {"USDC": 1_000_000_000},
            },
        ],
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit", "params": {}},
        },
        "clock": {
            "type": "solana_slot",
            "params": {
                "slot_duration_seconds": 0.4,
                "epoch_length_slots": 432_000,
                "skip_rate": 0.0,
            },
        },
        "num_rounds": 3,
        "snapshot_interval": 1,
        "seed": 4242,
    }

    engine = build_engine(spec)

    assert isinstance(engine._clock, SolanaSlotClock)

    schedule = engine._config.execution_model.leader_schedule
    assert isinstance(schedule, LeaderSchedule)
    # 1-validator default — every slot resolves to the same single leader.
    leaders = {schedule.leader_for_slot(slot) for slot in range(50)}
    assert len(leaders) == 1

    result = engine.run()
    assert result.num_rounds_executed == 3


def test_next_leader_slot_unknown_pubkey_raises() -> None:
    schedule = LeaderSchedule(
        [ValidatorStake(pubkey="A", stake_lamports=100)],
        seed=0,
        epoch_length_slots=1000,
    )
    with pytest.raises(ValueError):
        schedule.next_leader_slot("UNKNOWN", after_slot=0)
