"""Tip efficiency metric (PRD US-006 / line 968).

Tip efficiency = sum(tip_lamports) / sum(extracted_value_lamports).

By construction this rewards searchers who extract more value per unit of
tip paid. Zero extraction (no landed extractive bundles) returns 0.0 as a
sentinel rather than an undefined / infinite ratio — the chart layer treats
``sample_size`` to distinguish "no data" from "all-loss".
"""

from __future__ import annotations

from typing import Iterable

from .landing_rate import MetricResult


def compute_tip_efficiency(
    samples: Iterable[tuple[int, int]],
) -> MetricResult:
    """Compute aggregate tip efficiency from (tip, extracted_value) pairs.

    ``samples`` is an iterable of ``(tip_lamports, extracted_value_lamports)``
    pairs, one per landed extractive bundle. Both are integers in lamports.
    """
    sample_list = [(int(tip), int(ev)) for tip, ev in samples]
    total_tip = sum(tip for tip, _ in sample_list)
    total_ev = sum(ev for _, ev in sample_list)
    value = (total_tip / total_ev) if total_ev > 0 else 0.0
    return MetricResult(
        name="tip_efficiency",
        value=value,
        unit="ratio",
        sample_size=len(sample_list),
    )
