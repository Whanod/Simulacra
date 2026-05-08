"""Slot inclusion latency metric (PRD US-006 / line 969).

Inclusion latency = ``landed_slot - submitted_slot`` for landed bundles. Unlike
landing-rate / tip-efficiency this is a *distribution* metric — chart layers
need percentiles, not a single scalar — so the calculator returns
:class:`LatencyDistribution` carrying mean/median/p95/p99 plus the raw samples.
``MetricResult`` is also returned via ``.headline`` for callers that just want
the median exposed alongside the other six metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .landing_rate import MetricResult


@dataclass(frozen=True)
class LatencyDistribution:
    """Distribution of slot-inclusion latencies for landed bundles."""

    name: str
    unit: str
    sample_size: int
    mean: float
    median: float
    p95: float
    p99: float
    samples: tuple[int, ...]

    @property
    def headline(self) -> MetricResult:
        return MetricResult(
            name=self.name,
            value=self.median,
            unit=self.unit,
            sample_size=self.sample_size,
        )


def _percentile(sorted_samples: list[int], pct: float) -> float:
    """Linear-interpolated percentile, matching ``statistics.quantiles`` semantics
    closely enough for chart use without taking a numpy dependency."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return float(sorted_samples[0])
    rank = (pct / 100.0) * (len(sorted_samples) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = rank - lo
    return sorted_samples[lo] + (sorted_samples[hi] - sorted_samples[lo]) * frac


def compute_slot_inclusion_latency(
    samples: Iterable[tuple[int, int]],
) -> LatencyDistribution:
    """Compute inclusion-latency distribution from (submitted, landed) slot pairs.

    Pairs with ``landed_slot < submitted_slot`` are dropped — the bundle was
    not actually landed downstream of the submission. Latency is integer slots.
    """
    latencies = sorted(
        int(landed) - int(submitted)
        for submitted, landed in samples
        if int(landed) >= int(submitted)
    )
    if not latencies:
        return LatencyDistribution(
            name="slot_inclusion_latency",
            unit="slots",
            sample_size=0,
            mean=0.0,
            median=0.0,
            p95=0.0,
            p99=0.0,
            samples=(),
        )
    mean = sum(latencies) / len(latencies)
    return LatencyDistribution(
        name="slot_inclusion_latency",
        unit="slots",
        sample_size=len(latencies),
        mean=mean,
        median=_percentile(latencies, 50.0),
        p95=_percentile(latencies, 95.0),
        p99=_percentile(latencies, 99.0),
        samples=tuple(latencies),
    )
