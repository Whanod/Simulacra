"""``OracleSource`` ABC and Solana-shaped oracle implementations.

PRD US-006 step 1.8b: the legacy ``PriceFeed`` ABC and the
``LegacyFeedAsOracle`` shim are gone. Multi-token feed generators
(``HistoricalFeed`` / ``StochasticFeed`` / ``CompositeFeed``) project
per-token oracle views via ``oracle_for(token)`` directly — no shim
needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from defi_sim.core.types import Action, Numeric


class OracleSource(ABC):
    """Solana-shaped price-source interface.

    Implementations populate ``update_mode`` and ``confidence_interval`` as
    instance (or class) attributes. ``price_at`` returns ``(price,
    confidence)`` where confidence is the absolute +/- band on price.
    """

    update_mode: Literal["push", "pull"]
    confidence_interval: float

    @abstractmethod
    def price_at(self, slot: int) -> tuple[Numeric, float]:
        ...


class PushOracle(OracleSource):
    """Aggregator-published oracle (PRD US-006 line 459).

    Models Solana push-mode feeds (e.g. legacy Pyth Pull-before-Pull,
    Switchboard V2 crank): the aggregator publishes a new price every
    ``update_cadence_slots`` slots; consumers see the last-published
    value with staleness ``slot - last_update_slot`` (max staleness =
    ``update_cadence_slots - 1``). Update cost is borne by oracle
    operators, not consumers — surfaced here so 2.4 calibration / metrics
    can account for it.
    """

    update_mode: Literal["push", "pull"] = "push"

    def __init__(
        self,
        *,
        update_cadence_slots: int,
        update_cost_lamports: int,
        price_source: Callable[[int], Numeric],
        confidence_interval: float = 0.0,
    ):
        if update_cadence_slots <= 0:
            raise ValueError("update_cadence_slots must be positive")
        self.update_cadence_slots = int(update_cadence_slots)
        self.update_cost_lamports = int(update_cost_lamports)
        self.confidence_interval = float(confidence_interval)
        self._price_source = price_source

    def last_update_slot(self, slot: int) -> int:
        return (slot // self.update_cadence_slots) * self.update_cadence_slots

    def staleness(self, slot: int) -> int:
        return slot - self.last_update_slot(slot)

    def price_at(self, slot: int) -> tuple[Numeric, float]:
        published_at = self.last_update_slot(slot)
        return self._price_source(published_at), self.confidence_interval


@dataclass
class OracleUpdateAction(Action):
    """Pull-mode oracle price-update instruction (PRD US-006 line 477).

    Consumers include this in their tx so the oracle account is refreshed
    before the consuming instruction reads it. The CU cost flows through
    the standard :class:`~defi_sim.engine.gas.ComputeUnitCost` model via
    ``compute_unit_limit`` set by :meth:`PullOracle.pull`.
    """

    oracle_id: str = ""
    target_slot: int = 0


class PullOracle(OracleSource):
    """Consumer-pulled oracle (PRD US-006 line 467).

    Models Pyth Pull, Pyth Lazer, and Switchboard On-Demand: the consumer
    includes an :class:`OracleUpdateAction` in their tx to refresh the
    oracle account before reading it. The cached price returned by
    :meth:`price_at` only advances when :meth:`pull` is called — without an
    explicit pull, time may pass and the oracle stays frozen at its last
    pulled value. Freshness is bounded by ``staleness_tolerance_slots``;
    consumers should re-pull when ``is_stale(slot)`` is True.
    """

    update_mode: Literal["push", "pull"] = "pull"

    def __init__(
        self,
        *,
        oracle_id: str,
        update_cu_cost: int,
        update_lamport_cost: int,
        staleness_tolerance_slots: int,
        price_source: Callable[[int], Numeric],
        confidence_interval: float = 0.0,
        initial_pull_slot: int | None = None,
    ):
        if update_cu_cost <= 0:
            raise ValueError("update_cu_cost must be positive")
        if update_lamport_cost < 0:
            raise ValueError("update_lamport_cost must be non-negative")
        if staleness_tolerance_slots < 0:
            raise ValueError("staleness_tolerance_slots must be non-negative")
        self.oracle_id = oracle_id
        self.update_cu_cost = int(update_cu_cost)
        self.update_lamport_cost = int(update_lamport_cost)
        self.staleness_tolerance_slots = int(staleness_tolerance_slots)
        self.confidence_interval = float(confidence_interval)
        self._price_source = price_source
        self._last_pull_slot: int | None = None
        self._last_pull_price: Numeric | None = None
        if initial_pull_slot is not None:
            self._last_pull_slot = int(initial_pull_slot)
            self._last_pull_price = price_source(int(initial_pull_slot))

    def last_pull_slot(self) -> int | None:
        return self._last_pull_slot

    def staleness(self, slot: int) -> int | None:
        if self._last_pull_slot is None:
            return None
        return slot - self._last_pull_slot

    def is_stale(self, slot: int) -> bool:
        if self._last_pull_slot is None:
            return True
        return (slot - self._last_pull_slot) > self.staleness_tolerance_slots

    def price_at(self, slot: int) -> tuple[Numeric, float]:
        if self._last_pull_price is None:
            raise RuntimeError(
                "PullOracle has not been pulled; consumer must call pull(slot) first"
            )
        return self._last_pull_price, self.confidence_interval

    def pull(self, slot: int, *, agent_id: str = "oracle") -> OracleUpdateAction:
        self._last_pull_slot = int(slot)
        self._last_pull_price = self._price_source(int(slot))
        return OracleUpdateAction(
            agent_id=agent_id,
            oracle_id=self.oracle_id,
            target_slot=int(slot),
            compute_unit_limit=self.update_cu_cost,
        )


def passes_confidence_gate(
    price: Numeric, confidence: float, threshold: Numeric
) -> bool:
    """Confidence-interval gate for liquidations (PRD US-006 line 478).

    Returns True only when the lower bound of the oracle's confidence band
    (``price - confidence``) is strictly above ``threshold``. Liquidator
    agents (Phase 3.7) call this to avoid liquidating on a price that
    *might* be above the trigger but whose confidence band still straddles
    it.
    """
    return (price - confidence) > threshold
