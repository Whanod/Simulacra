"""Metric registry with batch and streaming support."""

from __future__ import annotations

from typing import Callable, Protocol

from defi_sim.core.types import MarketSnapshot, SimulationResult
from defi_sim.engine.events import Event, EventBus, EventType


class StreamingMetric(Protocol):
    """Metric that accumulates state across rounds."""
    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None: ...
    def finalize(self) -> float: ...


BatchMetric = Callable[[SimulationResult], float]


class MetricRegistry:
    def __init__(self):
        self._batch: dict[str, tuple[BatchMetric, bool, float]] = {}
        self._streaming: dict[str, tuple[StreamingMetric, bool, float]] = {}

    def register(self, name: str, fn: BatchMetric, lower_is_better: bool = True,
                 weight: float = 0.0) -> None:
        self._batch[name] = (fn, lower_is_better, weight)

    def register_streaming(self, name: str, metric: StreamingMetric,
                           lower_is_better: bool = True, weight: float = 0.0) -> None:
        self._streaming[name] = (metric, lower_is_better, weight)

    def unregister(self, name: str) -> None:
        self._batch.pop(name, None)
        self._streaming.pop(name, None)

    def compute_all(self, result: SimulationResult) -> dict[str, float]:
        """Compute all metrics."""
        values: dict[str, float] = {}

        for name, (fn, _, _) in self._batch.items():
            try:
                values[name] = fn(result)
            except Exception:
                values[name] = float('nan')

        for name, (metric, _, _) in self._streaming.items():
            try:
                values[name] = metric.finalize()
            except Exception:
                values[name] = float('nan')

        return values

    def normalize(self, raw: dict[str, float], all_runs: list[dict[str, float]]) -> dict[str, float]:
        """Pareto-normalize metrics across multiple runs."""
        result: dict[str, float] = {}
        for name, value in raw.items():
            all_vals = [r.get(name, 0.0) for r in all_runs]
            mn = min(all_vals)
            mx = max(all_vals)

            if mx > mn:
                normalized = (value - mn) / (mx - mn)
            else:
                normalized = 0.5

            # Flip direction
            lower_better = True
            if name in self._batch:
                lower_better = self._batch[name][1]
            elif name in self._streaming:
                lower_better = self._streaming[name][1]

            if lower_better:
                normalized = 1.0 - normalized

            result[name] = normalized

        return result

    def composite(self, normalized: dict[str, float]) -> float:
        """Compute weighted composite score."""
        total = 0.0
        weight_sum = 0.0

        for name, value in normalized.items():
            weight = 0.0
            if name in self._batch:
                weight = self._batch[name][2]
            elif name in self._streaming:
                weight = self._streaming[name][2]

            total += value * weight
            weight_sum += weight

        return total / weight_sum if weight_sum > 0 else 0.0

    def subscribe_to(self, bus: EventBus) -> None:
        """Wire streaming metrics to the event bus."""
        for name, (metric, _, _) in self._streaming.items():
            def make_handler(m):
                def handler(event: Event):
                    state = event.data.get("market_state")
                    if state is None:
                        state = event.data.get("all_market_states")
                    m.on_round(
                        event.round,
                        event.timestamp,
                        state,
                    )
                return handler
            bus.on(EventType.ROUND_END, make_handler(metric))
