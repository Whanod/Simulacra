"""Submission-path landing-probability comparison (PRD US-006 / line 973).

Bundles can reach a leader through several submission paths — Jito relay,
direct gRPC to the leader, public RPC fan-out, etc. This metric aggregates
landing outcomes per path and exposes the landing-probability comparison so
the chart layer (PRD line 982) can render a path-vs-path bar chart and the
headline strip can surface the spread between the best and worst path — the
load-bearing signal for "is your submission path costing you bundles?".

The headline scalar is ``best_landing_rate - worst_landing_rate``, projected
into the canonical :class:`MetricResult` shape so this metric sits alongside
the other six in run snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .landing_rate import MetricResult


@dataclass(frozen=True)
class SubmissionPathComparison:
    """Per-path landing counts and rates, sorted by path name."""

    name: str
    unit: str
    sample_size: int
    paths: tuple[str, ...]
    submitted: tuple[int, ...]
    landed: tuple[int, ...]
    landing_rates: tuple[float, ...]
    spread: float

    @property
    def headline(self) -> MetricResult:
        return MetricResult(
            name=self.name,
            value=self.spread,
            unit=self.unit,
            sample_size=self.sample_size,
        )


def compute_submission_path_comparison(
    samples: Iterable[tuple[str, bool]],
) -> SubmissionPathComparison:
    """Aggregate ``(path, landed)`` samples into per-path landing rates.

    Each input pair is one submission attempt: a bundle was sent via ``path``
    and either landed (``True``) or did not (``False``). Paths are sorted
    lexicographically so the output is deterministic. Per-path landing rate
    is ``landed / submitted``; the headline ``spread`` is the difference
    between the best and worst landing rate. A path with zero submissions is
    impossible by construction (it would not appear in the input), but for
    safety the rate is the 0.0 sentinel — same convention as ``tip_efficiency``
    and ``breakeven_curve`` for empty/degenerate inputs.
    """
    submitted_counts: dict[str, int] = {}
    landed_counts: dict[str, int] = {}
    for path, landed in samples:
        key = str(path)
        submitted_counts[key] = submitted_counts.get(key, 0) + 1
        if landed:
            landed_counts[key] = landed_counts.get(key, 0) + 1

    paths = tuple(sorted(submitted_counts))
    submitted = tuple(submitted_counts[p] for p in paths)
    landed = tuple(landed_counts.get(p, 0) for p in paths)
    landing_rates = tuple(
        (landed[i] / submitted[i]) if submitted[i] > 0 else 0.0
        for i in range(len(paths))
    )
    spread = (max(landing_rates) - min(landing_rates)) if landing_rates else 0.0
    sample_size = sum(submitted)

    return SubmissionPathComparison(
        name="submission_path_comparison",
        unit="ratio",
        sample_size=sample_size,
        paths=paths,
        submitted=submitted,
        landed=landed,
        landing_rates=landing_rates,
        spread=spread,
    )
