"""Bundle landing rate metric (PRD US-006 / line 967).

Landing rate = (#bundles with status="landed") / (#total bundles considered).
"Reverted" bundles are counted in the denominator because the auction selected
them; "dropped" bundles are also counted because they were submitted to the
auction. See :class:`defi_sim.core.types.BundleOutcome` for status semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from defi_sim.core.types import BundleOutcome


@dataclass(frozen=True)
class MetricResult:
    """Output of a replay metric calculator.

    ``value`` is the headline scalar. ``unit`` describes what the scalar is
    (e.g. ``"ratio"``, ``"lamports"``, ``"slots"``). ``sample_size`` lets
    downstream chart code distinguish "0 because no data" from "0 because all
    bundles dropped".
    """

    name: str
    value: float
    unit: str
    sample_size: int


def compute_bundle_landing_rate(outcomes: Iterable[BundleOutcome]) -> MetricResult:
    outcomes_list = list(outcomes)
    total = len(outcomes_list)
    landed = sum(1 for o in outcomes_list if o.status == "landed")
    value = landed / total if total > 0 else 0.0
    return MetricResult(
        name="bundle_landing_rate",
        value=value,
        unit="ratio",
        sample_size=total,
    )
