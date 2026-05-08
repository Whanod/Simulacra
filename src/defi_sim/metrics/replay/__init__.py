"""Replay+bundle metric calculators (PRD US-006 / line 978).

Each calculator is a pure function returning :class:`MetricResult`. The
canonical metric set is:

* bundle landing rate
* tip efficiency
* slot inclusion latency
* CU/$ tip break-even curve
* skip-rate cost
* write-lock contention heatmap
* submission-path landing probability comparison

Calculators are invoked from replay run snapshots (PRD US-002) and bundle
simulator responses (PRD US-005). The submodule is intentionally small and
free of engine-runtime dependencies — each function takes the minimal data
shape it needs so callers can adapt artifacts without coupling here.
"""

from __future__ import annotations

from .breakeven_curve import (
    BreakEvenCurve,
    compute_cu_per_dollar_tip_breakeven_curve,
)
from .landing_rate import MetricResult, compute_bundle_landing_rate
from .skip_rate_cost import compute_skip_rate_cost
from .slot_inclusion_latency import (
    LatencyDistribution,
    compute_slot_inclusion_latency,
)
from .submission_path_comparison import (
    SubmissionPathComparison,
    compute_submission_path_comparison,
)
from .tip_efficiency import compute_tip_efficiency
from .write_lock_heatmap import WriteLockHeatmap, compute_write_lock_heatmap

__all__ = [
    "BreakEvenCurve",
    "LatencyDistribution",
    "MetricResult",
    "SubmissionPathComparison",
    "WriteLockHeatmap",
    "compute_bundle_landing_rate",
    "compute_cu_per_dollar_tip_breakeven_curve",
    "compute_skip_rate_cost",
    "compute_slot_inclusion_latency",
    "compute_submission_path_comparison",
    "compute_tip_efficiency",
    "compute_write_lock_heatmap",
]
