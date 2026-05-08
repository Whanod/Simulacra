"""Skip-rate cost metric (PRD US-006 / line 971).

Skip-rate cost = sum of expected-value (lamports) for slots that the assigned
leader skipped. The denominator-free aggregation matches the framing in PRD
US-006: "lost EV from skipped slots" is a count of opportunity, not a ratio.
A zero-skip corpus returns the 0.0 sentinel — no skipped slots means no lost
value, never a divide-by-zero.
"""

from __future__ import annotations

from typing import Iterable

from .landing_rate import MetricResult


def compute_skip_rate_cost(
    samples: Iterable[tuple[bool, int]],
) -> MetricResult:
    """Compute skip-rate cost from ``(was_skipped, ev_lamports)`` slot records.

    Each pair represents one slot in the replay window. ``was_skipped`` is
    ``True`` when the leader failed to produce a block; ``ev_lamports`` is the
    total expected value (sum of would-be tip + would-be extraction) that
    would have been captured had the slot landed. Non-skipped slots are
    counted in ``sample_size`` but contribute zero to the cost — they're the
    "denominator" that makes the rate interpretable.
    """
    sample_list = [(bool(skipped), int(ev)) for skipped, ev in samples]
    cost = sum(ev for skipped, ev in sample_list if skipped)
    return MetricResult(
        name="skip_rate_cost",
        value=float(cost),
        unit="lamports",
        sample_size=len(sample_list),
    )
