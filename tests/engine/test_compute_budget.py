"""Compute-budget admission/drop-reason vocabulary (US-002, PRD line 166)."""

from __future__ import annotations

import logging

import pytest

from defi_sim.core.types import ComputeBudgetExhaustedEvent, SwapAction
from defi_sim.engine.compute_budget import ComputeBudget, ComputeBudgetSource
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.execution import (
    KNOWN_DROP_REASONS,
    DropReason,
    SolanaLikeExecution,
    _DEFER_WARNING_THRESHOLD,
)
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import ExecutedAction, SlotContext


def test_preset_current_returns_current_mainnet_caps() -> None:
    # PRD US-002 line 176: ComputeBudget.preset("current") returns the current caps.
    current = ComputeBudget.preset("current")
    assert current.per_slot == 60_000_000
    assert current.per_tx == 1_400_000
    assert current.per_writable_account == 12_000_000
    assert current.source is None


def test_compute_budget_defaults_match_current_mainnet() -> None:
    # PRD US-002 line 179: ComputeBudget() matches (60_000_000, 1_400_000, 12_000_000).
    budget = ComputeBudget()
    assert budget.per_slot == 60_000_000
    assert budget.per_tx == 1_400_000
    assert budget.per_writable_account == 12_000_000
    assert budget == ComputeBudget.preset("current")


def test_preset_rejects_historical_without_source_metadata() -> None:
    # PRD US-002 line 176: a historical preset is not accepted unless it
    # includes a source URL / activation slot.
    with pytest.raises(ValueError, match="source metadata"):
        ComputeBudget.register_preset(
            "no-source",
            ComputeBudget(per_slot=48_000_000, per_tx=1_200_000, per_writable_account=12_000_000),
        )

    src = ComputeBudgetSource(activation_slot=200_000_000, reference="SIMD-test-0176")
    sourced = ComputeBudget(
        per_slot=48_000_000,
        per_tx=1_200_000,
        per_writable_account=12_000_000,
        source=src,
    )
    ComputeBudget.register_preset("test-0176", sourced)
    try:
        looked_up = ComputeBudget.preset("test-0176")
        assert looked_up == sourced
        assert looked_up.source == src
    finally:
        ComputeBudget._PRESETS.pop("test-0176", None)


def test_preset_unknown_version_raises() -> None:
    with pytest.raises(ValueError, match="Unknown ComputeBudget preset"):
        ComputeBudget.preset("does-not-exist")


def test_compute_budget_historical_presets_are_source_backed() -> None:
    # PRD US-002 line 180: every preset other than "current" carries source
    # metadata (activation slot / proposal reference) and asserted values.
    snapshot = dict(ComputeBudget._PRESETS)
    sample_source = ComputeBudgetSource(
        activation_slot=240_000_000, reference="SIMD-test-0180"
    )
    sample_budget = ComputeBudget(
        per_slot=48_000_000,
        per_tx=1_200_000,
        per_writable_account=12_000_000,
        source=sample_source,
    )
    ComputeBudget.register_preset("test-0180", sample_budget)
    try:
        for version, preset in ComputeBudget._PRESETS.items():
            assert version != "current", (
                "'current' must not appear in _PRESETS — it's served by the "
                "default constructor path."
            )
            assert preset.source is not None, (
                f"Preset {version!r} is missing source metadata; non-current "
                "presets must cite activation slot + reference."
            )
            assert preset.source.activation_slot > 0
            assert preset.source.reference, (
                f"Preset {version!r} has empty source reference."
            )
            looked_up = ComputeBudget.preset(version)
            assert looked_up == preset
            assert looked_up.per_slot == preset.per_slot
            assert looked_up.per_tx == preset.per_tx
            assert looked_up.per_writable_account == preset.per_writable_account
    finally:
        ComputeBudget._PRESETS.clear()
        ComputeBudget._PRESETS.update(snapshot)


def test_drop_reason_vocabulary_registers_cu_budget_reasons() -> None:
    assert DropReason.CU_PER_TX_EXCEEDED == "cu_per_tx_exceeded"
    assert DropReason.CU_PER_SLOT_EXCEEDED == "cu_per_slot_exceeded"
    assert DropReason.CU_PER_ACCOUNT_EXCEEDED == "cu_per_account_exceeded"
    assert "cu_per_tx_exceeded" in KNOWN_DROP_REASONS
    assert "cu_per_slot_exceeded" in KNOWN_DROP_REASONS
    assert "cu_per_account_exceeded" in KNOWN_DROP_REASONS


def test_admit_uses_canonical_drop_reason_for_per_tx_cap() -> None:
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    oversized = SwapAction(agent_id="a0", compute_unit_limit=2_000_000)
    fine = SwapAction(agent_id="a1", compute_unit_limit=200_000)
    admitted, dropped = model.admit([oversized, fine], round=0, context=None)
    assert [a.agent_id for a in admitted] == ["a1"]
    assert len(dropped) == 1
    dropped_action, reason = dropped[0]
    assert dropped_action is oversized
    assert reason == DropReason.CU_PER_TX_EXCEEDED
    assert reason in KNOWN_DROP_REASONS


def test_per_tx_cap_drops_oversized_action() -> None:
    # PRD US-002 line 181: admit a single action with compute_unit_limit=1_500_000;
    # drop reason is cu_per_tx_exceeded (just over the 1_400_000 default cap).
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    oversized = SwapAction(agent_id="a0", compute_unit_limit=1_500_000)
    admitted, dropped = model.admit([oversized], round=0, context=None)
    assert admitted == []
    assert len(dropped) == 1
    dropped_action, reason = dropped[0]
    assert dropped_action is oversized
    assert reason == DropReason.CU_PER_TX_EXCEEDED
    assert reason in KNOWN_DROP_REASONS


def _run_one_slot(model: SolanaLikeExecution, slot: int, pending: list) -> list:
    """Drive ``execute_slot`` with a no-op executor; return SlotOutcome.deferred."""

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=slot,
        pending_actions=list(pending),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome = model.execute_slot(ctx)
    return outcome.deferred


def test_under_capacity_swaps_all_land_in_one_slot() -> None:
    # PRD US-002 line 174: 30 swaps × 200_000 CU = 6_000_000 < 60_000_000
    # default per-slot cap; all must land (none deferred, none dropped).
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=200_000) for i in range(30)
    ]

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=0,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome = model.execute_slot(ctx)

    assert outcome.deferred == []
    assert outcome.dropped == []
    assert len(outcome.executed) == 30
    assert {ex.action.agent_id for ex in outcome.executed} == {f"a{i}" for i in range(30)}


def test_overflow_swaps_defer_with_per_slot_cap_exceeded() -> None:
    # PRD US-002 line 175: 350 swaps × 200_000 CU = 70_000_000 > 60_000_000
    # default per-slot cap. 60M / 200K = 300 fit → at least 50 must be deferred.
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=200_000) for i in range(350)
    ]

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=0,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome = model.execute_slot(ctx)

    assert len(outcome.deferred) >= 50
    assert len(outcome.executed) <= 300
    # Total accounting: every admitted action is either executed or deferred.
    assert len(outcome.executed) + len(outcome.deferred) == 350
    assert outcome.dropped == []
    # The cu_per_slot_exceeded reason is registered in the canonical drop-reason
    # vocabulary so 1.2b / event emission can surface it.
    assert "cu_per_slot_exceeded" in KNOWN_DROP_REASONS


def test_per_slot_cap_defers_overflow() -> None:
    # PRD US-002 line 182: admit 350 × 200_000 CU actions; at least 50 are
    # deferred/dropped with cu_per_slot_exceeded (60M cap / 200K = 300 fit).
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    swaps = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=200_000) for i in range(350)
    ]

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=0,
        pending_actions=list(swaps),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome = model.execute_slot(ctx)

    overflowed = list(outcome.deferred) + [d[0] for d in outcome.dropped]
    assert len(overflowed) >= 50
    assert len(outcome.executed) + len(overflowed) == 350
    assert DropReason.CU_PER_SLOT_EXCEEDED == "cu_per_slot_exceeded"
    assert "cu_per_slot_exceeded" in KNOWN_DROP_REASONS


def test_event_emitted_on_each_exhaustion() -> None:
    # PRD US-002 line 183: for each per-tx and per-slot cap breach, exactly
    # one ComputeBudgetExhaustedEvent is emitted with the correct budget_kind.
    # Per-tx cap (1.4M default): one oversized action at 2_000_000.
    # Per-slot cap (60M default): 320 × 200_000 CU = 64M > 60M; 300 fit, 20+
    # are deferred — each deferral emits exactly one per_slot event.
    model = SolanaLikeExecution(compute_budget=ComputeBudget())
    oversized = SwapAction(agent_id="big", compute_unit_limit=2_000_000)
    fitters = [
        SwapAction(agent_id=f"a{i}", compute_unit_limit=200_000) for i in range(320)
    ]
    pending = [oversized, *fitters]

    captured: list[Event] = []

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=42,
        pending_actions=list(pending),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: captured.append(event),
    )
    outcome = model.execute_slot(ctx)

    cb_events = [e for e in captured if e.type == EventType.COMPUTE_BUDGET_EXHAUSTED]
    per_tx_events = [e for e in cb_events if e.data["budget_kind"] == "per_tx"]
    per_slot_events = [e for e in cb_events if e.data["budget_kind"] == "per_slot"]

    # Exactly one per_tx event for the oversized action.
    assert len(per_tx_events) == 1
    per_tx_payload = per_tx_events[0].data["compute_budget_exhausted"]
    assert isinstance(per_tx_payload, ComputeBudgetExhaustedEvent)
    assert per_tx_payload.budget_kind == "per_tx"
    assert per_tx_payload.slot == 42
    assert per_tx_payload.offender == "big"
    assert per_tx_payload.action is oversized
    assert per_tx_payload.attempted == 2_000_000
    assert per_tx_payload.remaining == 1_400_000

    # Exactly one per_slot event per deferred action — counts must match.
    assert len(per_slot_events) == len(outcome.deferred)
    assert len(per_slot_events) >= 20
    for event in per_slot_events:
        payload = event.data["compute_budget_exhausted"]
        assert isinstance(payload, ComputeBudgetExhaustedEvent)
        assert payload.budget_kind == "per_slot"
        assert payload.slot == 42
        assert payload.attempted == 200_000

    # Total CB events == per_tx + per_slot, no other budget_kinds emitted.
    assert len(cb_events) == len(per_tx_events) + len(per_slot_events)


def test_per_slot_counter_resets_on_new_slot() -> None:
    # PRD US-002 line 184: fill the slot; advance one slot; full budget
    # available again. 300 × 200_000 CU = 60_000_000 exactly fills the default
    # per-slot cap. After advancing to the next slot, the counter resets so a
    # second batch of 300 × 200_000 CU also fits without deferral.
    model = SolanaLikeExecution(compute_budget=ComputeBudget())

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    slot0_swaps = [
        SwapAction(agent_id=f"s0-a{i}", compute_unit_limit=200_000) for i in range(300)
    ]
    ctx0 = SlotContext(
        slot=0,
        pending_actions=list(slot0_swaps),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome0 = model.execute_slot(ctx0)
    assert len(outcome0.executed) == 300
    assert outcome0.deferred == []
    assert outcome0.dropped == []
    assert model._slot_cu_used == 60_000_000

    slot1_swaps = [
        SwapAction(agent_id=f"s1-a{i}", compute_unit_limit=200_000) for i in range(300)
    ]
    ctx1 = SlotContext(
        slot=1,
        pending_actions=list(slot1_swaps),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: None,
    )
    outcome1 = model.execute_slot(ctx1)
    assert len(outcome1.executed) == 300
    assert outcome1.deferred == []
    assert outcome1.dropped == []
    assert {ex.action.agent_id for ex in outcome1.executed} == {
        f"s1-a{i}" for i in range(300)
    }


def test_repeated_deferral_logs_warning_after_threshold(caplog) -> None:
    # Per-slot cap is 200_000; the action's CU footprint is 300_000 so it can
    # never fit and will be deferred every slot until 1.12 wires expiry.
    budget = ComputeBudget(per_slot=200_000, per_tx=1_400_000, per_writable_account=12_000_000)
    model = SolanaLikeExecution(compute_budget=budget)
    stuck = SwapAction(agent_id="stuck", compute_unit_limit=300_000)
    pending = [stuck]

    caplog.set_level(logging.WARNING, logger="defi_sim.engine.execution")

    # Run the same action through ``_DEFER_WARNING_THRESHOLD - 1`` slots; the
    # engine re-queues deferred actions, so we simulate that by feeding the
    # returned ``deferred`` list back in as next slot's pending.
    for slot in range(_DEFER_WARNING_THRESHOLD - 1):
        pending = _run_one_slot(model, slot, pending)
        assert pending == [stuck]

    # No warning yet — count has only reached threshold-1.
    assert not any("deferred" in rec.message for rec in caplog.records)

    # The slot that pushes the count to the threshold triggers exactly one warning.
    pending = _run_one_slot(model, _DEFER_WARNING_THRESHOLD - 1, pending)
    assert pending == [stuck]
    warnings = [rec for rec in caplog.records if "deferred" in rec.message]
    assert len(warnings) == 1
    assert "stuck" in warnings[0].getMessage()
    assert "PRD 1.12" in warnings[0].getMessage()

    # Further slots do not re-emit (warning fires only on the crossing).
    caplog.clear()
    for slot in range(_DEFER_WARNING_THRESHOLD, _DEFER_WARNING_THRESHOLD + 3):
        pending = _run_one_slot(model, slot, pending)
    assert not any("deferred" in rec.message for rec in caplog.records)


def test_defer_count_clears_when_action_fits(caplog) -> None:
    budget = ComputeBudget(per_slot=200_000, per_tx=1_400_000, per_writable_account=12_000_000)
    model = SolanaLikeExecution(compute_budget=budget)
    blocker = SwapAction(agent_id="blocker", compute_unit_limit=200_000)
    follower = SwapAction(agent_id="follower", compute_unit_limit=200_000)

    caplog.set_level(logging.WARNING, logger="defi_sim.engine.execution")

    # Slot 0: blocker fills the cap, follower is deferred (count=1).
    deferred = _run_one_slot(model, 0, [blocker, follower])
    assert deferred == [follower]
    assert model._defer_counts.get(id(follower)) == 1

    # Slot 1: only follower in queue, fits the (reset) per-slot cap; defer count cleared.
    deferred = _run_one_slot(model, 1, deferred)
    assert deferred == []
    assert id(follower) not in model._defer_counts
