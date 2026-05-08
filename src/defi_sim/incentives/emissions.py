"""Emission schedules for token distribution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from defi_sim.core.types import Numeric, TokenId


class EmissionSchedule(ABC):
    """Defines how reward tokens are minted over time."""

    @abstractmethod
    def rewards_for_period(self, start_timestamp: int, end_timestamp: int) -> dict[TokenId, Numeric]: ...

    @abstractmethod
    def total_remaining(self) -> dict[TokenId, Numeric]: ...


class FixedRateEmission(EmissionSchedule):
    """Constant emission rate per second."""

    def __init__(self, rates: dict[TokenId, Numeric], duration: int | None = None):
        self._rates = rates
        self._duration = duration
        self._total_emitted: dict[TokenId, Numeric] = {t: 0 for t in rates}
        self._elapsed_total: int = 0

    def rewards_for_period(self, start_timestamp: int, end_timestamp: int) -> dict[TokenId, Numeric]:
        elapsed = end_timestamp - start_timestamp
        if elapsed <= 0:
            return {}

        if self._duration is not None:
            remaining_seconds = max(0, self._duration - self._elapsed_total)
            elapsed = min(elapsed, remaining_seconds)
            if elapsed <= 0:
                return {}

        result: dict[TokenId, Numeric] = {}
        for token, rate in self._rates.items():
            if isinstance(rate, float):
                amount = rate * elapsed
            else:
                amount = rate * elapsed
            result[token] = amount
            self._total_emitted[token] = self._total_emitted.get(token, 0) + amount

        self._elapsed_total += elapsed
        return result

    def total_remaining(self) -> dict[TokenId, Numeric]:
        if self._duration is None:
            return {t: -1 for t in self._rates}  # infinite
        result: dict[TokenId, Numeric] = {}
        for token, rate in self._rates.items():
            total = rate * self._duration
            emitted = self._total_emitted.get(token, 0)
            result[token] = max(0, total - emitted)
        return result


class DecayingEmission(EmissionSchedule):
    """Emission rate decays by a factor each period (halving schedule, etc.)."""

    def __init__(self, initial_rates: dict[TokenId, Numeric],
                 decay_factor: float, decay_period: int):
        self._initial_rates = initial_rates
        self._decay_factor = decay_factor
        self._decay_period = decay_period
        self._total_emitted: dict[TokenId, Numeric] = {t: 0 for t in initial_rates}

    def rewards_for_period(self, start_timestamp: int, end_timestamp: int) -> dict[TokenId, Numeric]:
        elapsed = end_timestamp - start_timestamp
        if elapsed <= 0:
            return {}

        result: dict[TokenId, Numeric] = {}
        for token, rate in self._initial_rates.items():
            amount: Numeric = 0.0 if isinstance(rate, float) else 0
            segment_start = start_timestamp
            while segment_start < end_timestamp:
                period_index = segment_start // self._decay_period
                segment_end = min(end_timestamp, (period_index + 1) * self._decay_period)
                duration = segment_end - segment_start
                factor = self._decay_factor ** period_index

                if isinstance(rate, float):
                    amount += rate * factor * duration
                else:
                    amount += int(rate * factor * duration)

                segment_start = segment_end
            result[token] = amount
            self._total_emitted[token] = self._total_emitted.get(token, 0) + amount

        return result

    def total_remaining(self) -> dict[TokenId, Numeric]:
        result: dict[TokenId, Numeric] = {}
        for token, rate in self._initial_rates.items():
            if self._decay_factor >= 1.0:
                result[token] = -1
                continue
            if isinstance(rate, float):
                total = rate * self._decay_period / max(1.0 - self._decay_factor, 1e-12)
            else:
                total = int(rate * self._decay_period / max(1.0 - self._decay_factor, 1e-12))
            emitted = self._total_emitted.get(token, 0)
            remaining = total - emitted
            result[token] = remaining if remaining > 0 else 0
        return result


class CustomEmission(EmissionSchedule):
    """User-defined emission curve via a callable."""

    def __init__(self, fn: Callable[[int, int], dict[TokenId, Numeric]]):
        self._fn = fn

    def rewards_for_period(self, start_timestamp: int, end_timestamp: int) -> dict[TokenId, Numeric]:
        return self._fn(start_timestamp, end_timestamp)

    def total_remaining(self) -> dict[TokenId, Numeric]:
        return {}
