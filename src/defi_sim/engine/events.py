"""Extensible event system.

EventType registry, typed Event payloads, EventBus with filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable


class EventType(Enum):
    SIMULATION_START = auto()
    ROUND_START = auto()
    ACTION_EXECUTED = auto()
    ACTION_FAILED = auto()
    ACTION_DROPPED = auto()
    LP_FEES_DISTRIBUTED = auto()
    ROUND_END = auto()
    SIMULATION_END = auto()
    INTEREST_ACCRUED = auto()
    LIQUIDATION = auto()
    ORACLE_UPDATE = auto()
    ORACLE_STALE = auto()
    REWARD_DISTRIBUTED = auto()
    FUNDING_SETTLED = auto()
    EPOCH_BOUNDARY = auto()
    LST_RATE_UPDATED = auto()
    PARAMETER_CHANGED = auto()
    MARKET_ADDED = auto()
    MARKET_REMOVED = auto()
    SLOT_SKIPPED = auto()
    COMPUTE_BUDGET_EXHAUSTED = auto()
    PRIORITY_FEE_MARKET_UPDATED = auto()
    BLOCKHASH_EXPIRED = auto()
    FORK_REORG = auto()
    BUNDLE_TIP_PAID = auto()
    BUNDLE_TIP_REVERTED = auto()


CustomEventType = str


@dataclass
class Event:
    type: EventType | CustomEventType
    round: int
    timestamp: int | float
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    event_id: int | None = None


class EventBus:
    def __init__(self, record_history: bool = False, run_id: str | None = None):
        self._listeners: dict[EventType | CustomEventType, list[tuple[Callable[[Event], None], Callable[[Event], bool] | None]]] = {}
        self._any_listeners: list[tuple[Callable[[Event], None], Callable[[Event], bool] | None]] = []
        self._record_history = record_history
        self._history: list[Event] = []
        self._run_id = run_id
        self._next_event_id = 1

    def on(
        self,
        event_type: EventType | CustomEventType,
        callback: Callable[[Event], None],
        filter: Callable[[Event], bool] | None = None,
    ) -> None:
        """Subscribe to events. Optional filter predicate."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append((callback, filter))

    def emit(self, event: Event) -> None:
        """Emit an event to all subscribers."""
        if event.run_id is None:
            event.run_id = self._run_id
        if event.event_id is None:
            event.event_id = self._next_event_id
            self._next_event_id += 1
        self._decorate_event(event)
        if self._record_history:
            self._history.append(event)

        listeners = self._listeners.get(event.type, [])
        for callback, filt in listeners:
            if filt is None or filt(event):
                callback(event)
        for callback, filt in self._any_listeners:
            if filt is None or filt(event):
                callback(event)

    def on_any(
        self,
        callback: Callable[[Event], None],
        filter: Callable[[Event], bool] | None = None,
    ) -> None:
        """Subscribe to all events. Optional filter predicate."""
        self._any_listeners.append((callback, filter))

    def off(
        self,
        event_type: EventType | CustomEventType,
        callback: Callable[[Event], None],
    ) -> None:
        """Unsubscribe a callback."""
        if event_type in self._listeners:
            self._listeners[event_type] = [
                (cb, f) for cb, f in self._listeners[event_type] if cb is not callback
            ]

    def off_any(
        self,
        callback: Callable[[Event], None],
    ) -> None:
        """Unsubscribe an all-events callback."""
        self._any_listeners = [
            (cb, f) for cb, f in self._any_listeners if cb is not callback
        ]

    def replay(self, events: list[Event]) -> None:
        """Re-emit a sequence of recorded events."""
        for event in events:
            self.emit(event)

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    def _decorate_event(self, event: Event) -> None:
        data = event.data
        if event.run_id is not None:
            data.setdefault("run_id", event.run_id)

        action_event_types = {
            EventType.ACTION_EXECUTED,
            EventType.ACTION_FAILED,
            EventType.ACTION_DROPPED,
        }
        if event.type in action_event_types:
            action = data.get("action")
            data.setdefault("correlation_kind", "action")
            data.setdefault("correlation_id", f"{event.run_id or 'run'}:action:{event.event_id}")
            if action is not None:
                data.setdefault("action_type", action.__class__.__name__)
                market_name = getattr(action, "market_name", None)
                if market_name is not None:
                    data.setdefault("market_name", market_name)
                inner_action = getattr(action, "inner", None)
                if inner_action is not None:
                    data.setdefault("inner_action_type", inner_action.__class__.__name__)

        if event.type == EventType.PARAMETER_CHANGED:
            data.setdefault("correlation_kind", "parameter_change")
            data.setdefault("correlation_id", f"{event.run_id or 'run'}:parameter_change:{event.event_id}")
            data.setdefault("parameter_key", data.get("key"))
