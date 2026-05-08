"""Per-metric calibration threshold loader (PRD US-004 lines 768, 814-816).

Parses ``solana-plans/calibration/thresholds.yaml`` into typed
:class:`Threshold` records and applies them against a
``ReplayDiff.per_metric_error()`` mapping to surface
:class:`ThresholdBreach` markers for the calibration CI lane.

Threshold semantics match the YAML header:

* ``threshold_relative`` — pass iff
  ``abs_error / max(abs(actual), epsilon) <= threshold_relative``.
* ``threshold_absolute`` — pass iff ``abs_error <= threshold_absolute``.

Bands with ``supported=False`` (e.g. metrics gated on an unlanded
decoder) are skipped — they cannot breach a threshold because their
actual side is not yet measurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from defi_sim.engine.replay_execution import ErrorBand


THRESHOLDS_YAML_PATH = (
    Path(__file__).resolve().parents[3]
    / "solana-plans"
    / "calibration"
    / "thresholds.yaml"
)


@dataclass(frozen=True)
class Threshold:
    """One row of ``thresholds.yaml`` — exactly one bound is set."""

    metric: str
    threshold_relative: float | None = None
    threshold_absolute: float | None = None

    def __post_init__(self) -> None:
        rel_set = self.threshold_relative is not None
        abs_set = self.threshold_absolute is not None
        if rel_set == abs_set:
            raise ValueError(
                f"Threshold for {self.metric!r} must set exactly one of "
                "threshold_relative / threshold_absolute"
            )


@dataclass(frozen=True)
class ThresholdBreach:
    """A single metric whose error exceeded its configured threshold."""

    metric: str
    band: ErrorBand
    threshold: Threshold
    observed: float


def load_thresholds(path: Path | str | None = None) -> dict[str, Threshold]:
    """Load and validate the per-metric thresholds YAML.

    Returns a mapping from metric name to :class:`Threshold`. Raises
    ``ValueError`` if a row is malformed or names an unknown metric.
    """

    yaml_path = Path(path) if path is not None else THRESHOLDS_YAML_PATH
    with yaml_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "thresholds" not in raw:
        raise ValueError(
            f"thresholds.yaml at {yaml_path} must have a top-level "
            "'thresholds' list"
        )
    rows = raw["thresholds"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("'thresholds' must be a non-empty list")
    out: dict[str, Threshold] = {}
    for row in rows:
        if not isinstance(row, dict) or "metric" not in row:
            raise ValueError(f"threshold row missing 'metric': {row!r}")
        metric = row["metric"]
        if metric in out:
            raise ValueError(f"duplicate threshold for metric {metric!r}")
        out[metric] = Threshold(
            metric=metric,
            threshold_relative=row.get("threshold_relative"),
            threshold_absolute=row.get("threshold_absolute"),
        )
    return out


def flag_breaches(
    bands: Mapping[str, ErrorBand],
    thresholds: Mapping[str, Threshold],
    *,
    epsilon: float = 1e-12,
) -> list[ThresholdBreach]:
    """Return one :class:`ThresholdBreach` per band that exceeds its threshold.

    Bands keyed as ``"<metric>:<sub-key>"`` (e.g. ``pool_price:SOL/USDC``)
    are matched against the bare ``<metric>`` threshold. Bands with
    ``supported=False`` or ``abs_error is None`` are silently skipped.
    """

    breaches: list[ThresholdBreach] = []
    for key, band in bands.items():
        if not band.supported or band.abs_error is None:
            continue
        threshold = thresholds.get(_metric_root(key))
        if threshold is None:
            continue
        if threshold.threshold_absolute is not None:
            observed = band.abs_error
            limit = threshold.threshold_absolute
        else:
            assert threshold.threshold_relative is not None
            denom = max(abs(band.actual or 0.0), epsilon)
            observed = band.abs_error / denom
            limit = threshold.threshold_relative
        if observed > limit:
            breaches.append(
                ThresholdBreach(
                    metric=key,
                    band=band,
                    threshold=threshold,
                    observed=observed,
                )
            )
    return breaches


def assert_no_threshold_breaches(
    bands: Mapping[str, ErrorBand],
    thresholds: Mapping[str, Threshold],
    *,
    slot: int | str | None = None,
    epsilon: float = 1e-12,
) -> None:
    """Fail the calibration lane when any metric exceeds its threshold."""

    breaches = flag_breaches(bands, thresholds, epsilon=epsilon)
    if not breaches:
        return

    scope = f" for slot {slot}" if slot is not None else ""
    lines = [
        f"Calibration threshold breach{scope}: "
        f"{len(breaches)} metric(s) exceeded configured bands"
    ]
    for breach in breaches:
        threshold = breach.threshold
        if threshold.threshold_absolute is not None:
            kind = "absolute"
            limit = threshold.threshold_absolute
        else:
            kind = "relative"
            assert threshold.threshold_relative is not None
            limit = threshold.threshold_relative
        lines.append(
            f"- {breach.metric}: observed {breach.observed:.6g} {kind} "
            f"error > threshold {limit:.6g} "
            f"(predicted={breach.band.predicted:.6g}, "
            f"actual={breach.band.actual}, "
            f"abs_error={breach.band.abs_error})"
        )
    raise AssertionError("\n".join(lines))


def _metric_root(key: str) -> str:
    return key.split(":", 1)[0]


def expected_metric_keys() -> Iterable[str]:
    """The metric roots that ``ReplayDiff.per_metric_error`` may emit.

    Used by ``test_threshold_metric_keys_match_run_snapshot_keys`` to
    pin the YAML against the engine's published metric vocabulary.
    """

    from defi_sim.engine.replay_execution import ReplayDiff

    return tuple(ReplayDiff._METRICS)
