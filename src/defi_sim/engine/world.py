"""Multi-market composition.

World — container for multiple named markets.
WorldContext — extended decision context for multi-market agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from defi_sim.core.agent import DecisionContext
from defi_sim.core.market import Market
from defi_sim.core.types import MarketSnapshot
from defi_sim.engine.events import Event, EventBus, EventType


@dataclass
class WorldContext(DecisionContext):
    """Extended context for agents operating across multiple markets."""
    all_markets: dict[str, MarketSnapshot] = field(default_factory=dict)


class World:
    """Container for multiple named markets."""

    def __init__(self) -> None:
        self.markets: dict[str, Market] = {}
        self._event_bus: EventBus | None = None
        self._round_provider: Callable[[], int] | None = None
        self._timestamp_provider: Callable[[], int | float] | None = None

    def attach_event_bus(
        self,
        bus: EventBus,
        *,
        round_provider: Callable[[], int],
        timestamp_provider: Callable[[], int | float],
    ) -> None:
        self._event_bus = bus
        self._round_provider = round_provider
        self._timestamp_provider = timestamp_provider

    def _emit(self, event_type: EventType, **data: object) -> None:
        if self._event_bus is None or self._round_provider is None or self._timestamp_provider is None:
            return
        self._event_bus.emit(Event(
            type=event_type,
            round=self._round_provider(),
            timestamp=self._timestamp_provider(),
            data=dict(data),
        ))

    def add_market(self, name: str, market: Market) -> None:
        self.markets[name] = market
        self._emit(EventType.MARKET_ADDED, name=name, market_state=market.get_state())

    def remove_market(self, name: str) -> None:
        market = self.markets[name]
        del self.markets[name]
        self._emit(EventType.MARKET_REMOVED, name=name, market_state=market.get_state())

    def get_market(self, name: str) -> Market:
        return self.markets[name]

    def get_all_states(self) -> dict[str, MarketSnapshot]:
        return {name: market.get_state() for name, market in self.markets.items()}
