"""Admit-time blockhash-expiry tests (PRD US-014 lines 1134-1137).

The engine drops actions whose ``recent_blockhash`` is older than the
~150-slot validity window (PRD line 1108) and emits
``BlockhashExpiredEvent``. The blockhash machinery itself ships in
``defi_sim/engine/blockhash.py`` (line 1101) and the wiring sits in
``SolanaLikeExecution.admit`` (line 1108).
"""

from __future__ import annotations

from defi_sim.core.types import BlockhashExpiredEvent, SwapAction
from defi_sim.engine.blockhash import BlockhashHistory
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.execution import DropReason, SolanaLikeExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import ExecutedAction, SlotContext


def test_action_with_fresh_blockhash_admitted() -> None:
    """An action whose ``recent_blockhash`` is within the 150-slot
    validity window is admitted (PRD line 1129).
    """
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-slot-0")
    model = SolanaLikeExecution(blockhash_history=history)

    action = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        recent_blockhash="bh-slot-0",
    )

    admitted, dropped = model.admit([action], round=100)

    assert admitted == [action]
    assert dropped == []
    assert all(reason != DropReason.BLOCKHASH_EXPIRED for _, reason in dropped)


def test_action_with_stale_blockhash_dropped() -> None:
    """An action whose ``recent_blockhash`` is older than the 150-slot
    validity window is dropped with reason ``BLOCKHASH_EXPIRED`` (PRD
    lines 1128, 1135).
    """
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-slot-0")
    # Force the rolling window to evict bh-slot-0 by recording a fresher
    # blockhash past the validity boundary.
    history.record(slot=200, blockhash="bh-slot-200")
    model = SolanaLikeExecution(blockhash_history=history)

    action = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        recent_blockhash="bh-slot-0",
    )

    admitted, dropped = model.admit([action], round=200)

    assert admitted == []
    assert dropped == [(action, DropReason.BLOCKHASH_EXPIRED)]


def test_blockhash_expired_event_emitted() -> None:
    """``execute_slot`` emits a ``BlockhashExpiredEvent`` for each
    admit-time blockhash-expired drop (PRD line 1108, line 1136).
    """
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-slot-0")
    history.record(slot=200, blockhash="bh-slot-200")
    model = SolanaLikeExecution(blockhash_history=history)

    stale_action = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        recent_blockhash="bh-slot-0",
    )

    captured: list[Event] = []

    def executor(action, slot_index):
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
        )

    ctx = SlotContext(
        slot=200,
        pending_actions=[stale_action],
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda event: captured.append(event),
    )
    model.execute_slot(ctx)

    bh_events = [e for e in captured if e.type == EventType.BLOCKHASH_EXPIRED]
    assert len(bh_events) == 1
    payload = bh_events[0].data["blockhash_expired"]
    assert isinstance(payload, BlockhashExpiredEvent)
    assert payload.slot == 200
    assert payload.action is stale_action
    assert payload.blockhash == "bh-slot-0"
    assert bh_events[0].data["blockhash"] == "bh-slot-0"
    assert bh_events[0].round == 200


def test_explicit_expiry_slot_dropped_when_crossed() -> None:
    """PRD US-014 line 1099: ``expiry_slot`` is honored as a hard ceiling
    even when the blockhash itself is still inside the rolling window.
    """
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-0")
    model = SolanaLikeExecution(blockhash_history=history)

    action = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        recent_blockhash="bh-0",
        expiry_slot=10,
    )

    admitted, dropped = model.admit([action], round=11)

    assert admitted == []
    assert dropped == [(action, DropReason.BLOCKHASH_EXPIRED)]


def test_explicit_expiry_slot_admitted_when_not_yet_crossed() -> None:
    """``expiry_slot`` accepts the action up to and including its slot."""
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-0")
    model = SolanaLikeExecution(blockhash_history=history)

    action = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        recent_blockhash="bh-0",
        expiry_slot=10,
    )

    admitted, dropped = model.admit([action], round=10)

    assert admitted == [action]
    assert dropped == []


def test_default_none_blockhash_resolves_to_latest() -> None:
    """PRD US-014 line 1098: ``recent_blockhash=None`` means "use latest".

    Resolution at admit-time pins expiry to the most recent recorded
    blockhash so the default path can't dodge the validity window.
    """
    history = BlockhashHistory()
    history.record(slot=0, blockhash="bh-0")
    history.record(slot=200, blockhash="bh-200")
    model = SolanaLikeExecution(blockhash_history=history)

    # No ``recent_blockhash`` set — engine resolves to the latest
    # (bh-200 at slot 200), so submitting at slot 250 is well within
    # the 150-slot window.
    fresh = SwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
    )
    admitted, dropped = model.admit([fresh], round=250)
    assert admitted == [fresh]
    assert dropped == []


def test_blockhash_history_maintains_rolling_window() -> None:
    """``BlockhashHistory`` evicts entries whose slot is older than
    ``validity_slots`` relative to the most recently recorded slot
    (PRD US-014 line 1101, line 1137).
    """
    history = BlockhashHistory()
    assert len(history) == 0

    history.record(slot=0, blockhash="bh-0")
    history.record(slot=50, blockhash="bh-50")
    history.record(slot=100, blockhash="bh-100")

    assert len(history) == 3
    assert history.latest() == "bh-100"
    assert history.slot_of("bh-0") == 0
    assert history.slot_of("bh-50") == 50
    assert history.slot_of("bh-100") == 100

    # Recording at slot 151 evicts bh-0 (151 - 0 = 151 > 150) but keeps
    # bh-50 (151 - 50 = 101 <= 150) and bh-100.
    history.record(slot=151, blockhash="bh-151")

    assert len(history) == 3
    assert history.latest() == "bh-151"
    assert history.slot_of("bh-0") is None
    assert history.slot_of("bh-50") == 50
    assert history.slot_of("bh-100") == 100
    assert history.slot_of("bh-151") == 151

    # is_expired tracks the same window: bh-0 (evicted) is expired
    # against current_slot=151; bh-50 is still fresh.
    assert history.is_expired("bh-0", current_slot=151) is True
    assert history.is_expired("bh-50", current_slot=151) is False

    # Jumping far ahead evicts the rest of the recorded entries.
    history.record(slot=10_000, blockhash="bh-10000")
    assert len(history) == 1
    assert history.latest() == "bh-10000"
    assert history.slot_of("bh-50") is None
    assert history.slot_of("bh-100") is None
    assert history.slot_of("bh-151") is None
