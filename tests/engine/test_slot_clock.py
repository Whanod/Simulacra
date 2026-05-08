"""SolanaSlotClock unit tests (PRD US-001).

Locks the slot-clock primitive to its spec: 0.4 s slot, 432_000-slot epoch,
0.0 default skip rate, one-tick-per-slot advance, deterministic skip
emission, and epoch rollover at the configured boundary.
"""

from __future__ import annotations

import math

from defi_sim.core.clock import SolanaSlotClock
from defi_sim.core.types import SlotEvent, SlotSkippedEvent


def test_slot_clock_default_construction() -> None:
    clock = SolanaSlotClock()
    assert clock.slot_duration_seconds == 0.4
    assert clock.epoch_length_slots == 432_000
    assert clock.skip_rate == 0.0
    assert clock.current_slot == 0
    assert clock.current_epoch == 0


def test_slot_clock_advances_one_slot_per_tick() -> None:
    clock = SolanaSlotClock()
    for _ in range(100):
        clock.tick()
    assert clock.current_slot == 100


def test_slot_clock_emits_skip_event_at_configured_rate() -> None:
    always_skip = SolanaSlotClock(skip_rate=1.0, seed=0)
    for _ in range(50):
        event = always_skip.tick()
        assert isinstance(event, SlotSkippedEvent)

    never_skip = SolanaSlotClock(skip_rate=0.0, seed=0)
    for _ in range(50):
        event = never_skip.tick()
        assert isinstance(event, SlotEvent)
        assert not isinstance(event, SlotSkippedEvent)


def test_slot_clock_skip_rate_within_two_sigma_over_100k_ticks() -> None:
    """PRD validation: skip_rate=0.05 over 100k ticks within ±2σ of 5000."""
    n = 100_000
    p = 0.05
    expected = n * p
    sigma = math.sqrt(n * p * (1 - p))
    tolerance = 2 * sigma

    clock = SolanaSlotClock(skip_rate=p, seed=42)
    skipped = 0
    for _ in range(n):
        if isinstance(clock.tick(), SlotSkippedEvent):
            skipped += 1

    assert abs(skipped - expected) <= tolerance, (
        f"skipped={skipped} not within ±2σ ({tolerance:.1f}) of {expected}"
    )


def test_slot_clock_epoch_rolls_at_boundary() -> None:
    clock = SolanaSlotClock(
        slot_duration_seconds=0.4,
        epoch_length_slots=432_000,
        skip_rate=0.0,
    )
    for _ in range(432_000):
        clock.tick()
    assert clock.current_slot == 432_000
    assert clock.current_epoch == 1


def test_slot_clock_timestamp_returns_fractional_seconds() -> None:
    """Solana's 0.4 s slot duration produces non-integer timestamps; downstream
    consumers (Event, RoundSnapshot, DecisionContext, ExecutionContext) must
    tolerate fractional seconds without coercion."""
    from dataclasses import is_dataclass

    from defi_sim.core.agent import DecisionContext
    from defi_sim.core.types import ExecutionContext, RoundSnapshot, AgentState
    from defi_sim.engine.events import Event, EventType

    clock = SolanaSlotClock(slot_duration_seconds=0.4)
    # Slot 1 -> 0.4 s, slot 3 -> 1.2 s; both non-integer in float space.
    ts1 = clock.timestamp(1)
    ts3 = clock.timestamp(3)
    assert isinstance(ts1, float)
    assert ts1 == 0.4
    assert math.isclose(ts3, 1.2)
    elapsed = clock.elapsed(1, 3)
    assert isinstance(elapsed, float)
    assert math.isclose(elapsed, 0.8)

    # Downstream dataclasses accept the fractional timestamp without raising.
    event = Event(type=EventType.ROUND_START, round=1, timestamp=ts1)
    assert event.timestamp == 0.4
    snap = RoundSnapshot(round=1, timestamp=ts1)
    assert snap.timestamp == 0.4
    decision = DecisionContext(timestamp=ts1)
    assert decision.timestamp == 0.4
    exec_ctx = ExecutionContext(agent_state=AgentState(agent_id="a"), timestamp=ts1)
    assert exec_ctx.timestamp == 0.4
    assert is_dataclass(exec_ctx)
