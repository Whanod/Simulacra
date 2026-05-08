"""Write-lock contention heatmap metric (PRD US-006 / lines 972, 986).

Per PRD line 986 the chart renders per-account write-lock count over the slot
range with account on the y-axis, slot on the x-axis, and color encoding lock
count. The calculator returns :class:`WriteLockHeatmap` carrying the
``(account, slot) -> count`` aggregation plus the sorted axes the chart needs.
The headline scalar is the maximum single-cell contention, which is the
metric's load-bearing signal — "how badly does the most-contended account
stall a slot?" — projected into the canonical ``MetricResult`` shape so it
sits alongside the other six metrics in run snapshots.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .landing_rate import MetricResult


@dataclass(frozen=True)
class WriteLockHeatmap:
    """Per-(account, slot) write-lock counts, sorted axes, headline = max."""

    name: str
    unit: str
    sample_size: int
    accounts: tuple[str, ...]
    slots: tuple[int, ...]
    counts: dict[tuple[str, int], int]
    max_contention: int

    @property
    def headline(self) -> MetricResult:
        return MetricResult(
            name=self.name,
            value=float(self.max_contention),
            unit=self.unit,
            sample_size=self.sample_size,
        )


def compute_write_lock_heatmap(
    claims: Iterable[tuple[str, int]],
) -> WriteLockHeatmap:
    """Aggregate write-lock claims into the heatmap's ``(account, slot)`` grid.

    Each input pair is one write-lock claim — a transaction took a write lock
    on ``account`` during ``slot``. Multiple claims for the same cell stack
    (that *is* the contention signal). Output axes are sorted: accounts
    ascending lexicographically, slots ascending numerically, so the chart
    layer can render a stable grid without re-sorting.
    """
    counter: Counter[tuple[str, int]] = Counter()
    for account, slot in claims:
        counter[(str(account), int(slot))] += 1

    counts = dict(counter)
    accounts = tuple(sorted({account for account, _ in counts}))
    slots = tuple(sorted({slot for _, slot in counts}))
    max_contention = max(counts.values()) if counts else 0
    sample_size = sum(counts.values())

    return WriteLockHeatmap(
        name="write_lock_heatmap",
        unit="locks",
        sample_size=sample_size,
        accounts=accounts,
        slots=slots,
        counts=counts,
        max_contention=max_contention,
    )
