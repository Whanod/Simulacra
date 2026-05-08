"""CU/$ tip break-even curve metric (PRD US-006 / lines 970, 984).

Per PRD line 984 the chart is a scatter of ``tip_paid`` (lamports) versus
``extracted_value`` (lamports) per landed bundle, with the break-even line
defined by ``tip == ev``. This module returns the underlying sorted curve so
the chart layer (PRD line 1007) can render the scatter and overlay the
break-even reference line.

The curve is sorted ascending by tip — the x-axis of the scatter — which
makes the structural property "tips are monotonically non-decreasing" a
load-bearing invariant of the calculator's output. ``ratios`` exposes
``ev / tip`` per landed bundle (with the same sort order) so callers that
only want the headline "what fraction of landed bundles cleared break-even?"
can read it from ``headline`` without recomputing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .landing_rate import MetricResult


@dataclass(frozen=True)
class BreakEvenCurve:
    """Sorted (tip, extracted_value) curve for the break-even scatter."""

    name: str
    unit: str
    sample_size: int
    tips: tuple[int, ...]
    extracted_values: tuple[int, ...]
    ratios: tuple[float, ...]

    @property
    def headline(self) -> MetricResult:
        """Fraction of landed bundles that cleared break-even (``ev >= tip``)."""
        if self.sample_size == 0:
            value = 0.0
        else:
            cleared = sum(1 for ev, tip in zip(self.extracted_values, self.tips) if ev >= tip)
            value = cleared / self.sample_size
        return MetricResult(
            name=self.name,
            value=value,
            unit="ratio",
            sample_size=self.sample_size,
        )


def compute_cu_per_dollar_tip_breakeven_curve(
    samples: Iterable[tuple[int, int]],
) -> BreakEvenCurve:
    """Build the break-even scatter curve from ``(tip, extracted_value)`` pairs.

    Each pair is one landed bundle, both values in lamports. Output is sorted
    ascending by tip so the curve is monotonic on the x-axis.
    """
    sorted_pairs = sorted(
        ((int(tip), int(ev)) for tip, ev in samples),
        key=lambda pair: pair[0],
    )
    tips = tuple(tip for tip, _ in sorted_pairs)
    evs = tuple(ev for _, ev in sorted_pairs)
    ratios = tuple((ev / tip) if tip > 0 else 0.0 for tip, ev in sorted_pairs)
    return BreakEvenCurve(
        name="cu_per_dollar_tip_breakeven",
        unit="lamports",
        sample_size=len(sorted_pairs),
        tips=tips,
        extracted_values=evs,
        ratios=ratios,
    )
