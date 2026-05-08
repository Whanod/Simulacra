"""Tests for oracle staleness events + per-slot cost roll-up.

Covers PRD US-006 line 494 — surface oracle staleness and update cost
in events / metrics.
"""

from __future__ import annotations

from defi_sim.engine.events import EventBus, EventType
from defi_sim.engine.oracles import (
    OracleSlotCost,
    PullOracle,
    PushOracle,
    make_oracle_stale_event,
    oracle_costs_per_slot,
)


def test_make_oracle_stale_event_carries_prd_payload_shape():
    """PRD line 495: ``OracleStaleEvent(slot, oracle_id, last_update_slot)``."""
    event = make_oracle_stale_event(
        round=42,
        timestamp=42_000_000,
        slot=100,
        oracle_id="pyth_pull_sol_usdc",
        last_update_slot=80,
    )

    assert event.type is EventType.ORACLE_STALE
    assert event.round == 42
    assert event.data["slot"] == 100
    assert event.data["oracle_id"] == "pyth_pull_sol_usdc"
    assert event.data["last_update_slot"] == 80


def test_oracle_stale_event_preserves_none_last_update_slot():
    """A pull-mode oracle that has never been pulled has no prior update slot."""
    event = make_oracle_stale_event(
        round=0,
        timestamp=0,
        slot=0,
        oracle_id="switchboard_on_demand_sol_usdc",
        last_update_slot=None,
    )
    assert event.data["last_update_slot"] is None


def test_oracle_stale_event_flows_through_event_bus():
    """The event uses the existing ``EventBus`` machinery — no parallel pipeline."""
    bus = EventBus(record_history=True, run_id="run-1")
    received: list[dict[str, object]] = []
    bus.on(EventType.ORACLE_STALE, lambda e: received.append(dict(e.data)))

    bus.emit(
        make_oracle_stale_event(
            round=1,
            timestamp=1_000,
            slot=10,
            oracle_id="pyth_lazer_sol_usdc",
            last_update_slot=5,
        )
    )

    assert len(received) == 1
    assert received[0]["oracle_id"] == "pyth_lazer_sol_usdc"
    assert received[0]["last_update_slot"] == 5


def test_oracle_costs_per_slot_sums_pull_oracle_pulls():
    """PRD line 496: per-slot oracle-cost line aggregates consumer pull costs."""
    oracle = PullOracle(
        oracle_id="SOL/USD",
        update_cu_cost=15_000,
        update_lamport_cost=2_000,
        staleness_tolerance_slots=10,
        price_source=lambda _slot: 100,
    )

    costs = oracle_costs_per_slot(
        pull_oracle_pulls={"SOL/USD": [5, 5, 12]},
        pull_oracles={"SOL/USD": oracle},
    )

    assert costs == [
        OracleSlotCost(slot=5, cu=30_000, lamports=4_000, operator_lamports=0),
        OracleSlotCost(slot=12, cu=15_000, lamports=2_000, operator_lamports=0),
    ]


def test_oracle_costs_per_slot_includes_push_operator_cost_when_window_supplied():
    """Push-mode operator cost is surfaced separately from consumer-pull cost."""
    push = PushOracle(
        update_cadence_slots=10,
        update_cost_lamports=750,
        price_source=lambda _slot: 1,
    )

    costs = oracle_costs_per_slot(
        pull_oracle_pulls={},
        pull_oracles={},
        push_oracles={"SOL/USD-push": push},
        push_slot_window=(0, 30),
    )

    # Slots 0, 10, 20 — three operator-paid republishes inside [0, 30).
    assert [c.slot for c in costs] == [0, 10, 20]
    assert all(c.cu == 0 and c.lamports == 0 for c in costs)
    assert all(c.operator_lamports == 750 for c in costs)


def test_oracle_costs_per_slot_returns_empty_when_no_pulls_or_window():
    assert oracle_costs_per_slot(pull_oracle_pulls={}, pull_oracles={}) == []
