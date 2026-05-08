"""Jito tip-percentile prior calibration (FIX-020).

Reads a corpus of captured Jito bundles (produced by
``tools/cache_jito_bundles.py``) and fits a per-cohort empirical CDF over
in-cohort tip lamports. The fitted curve is the prior the
``BundleAuction`` blends against in-process tip observations: when a fresh
run has not yet seen any winning tips for a hot cohort, the auction can
quote a sensible percentile pulled from this calibrated baseline rather
than collapsing to the configured Jito floor.

The fit is intentionally simple: we store a small set of percentile
breakpoints (25 / 50 / 75 / 90 / 95 / 99) per cohort plus a population
fallback, and look up by linear interpolation between adjacent
breakpoints. We avoid scipy / heavy fits because (a) the underlying
distribution is heavy-tailed and structurally noisy at the high end, so a
parametric fit hides more than it captures, and (b) the auction caller
needs cheap, deterministic percentile lookups every slot.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

__all__ = [
    "DEFAULT_PERCENTILES",
    "TipQuoteCurve",
    "fit_tip_quote_curve",
    "iter_bundle_rows",
    "load_tip_quote_curve",
    "render_tip_quote_curve_yaml",
]


# Percentile breakpoints stored on disk. p25/50/75/90/95/99 covers the
# range BundleAuction.tip_quote actually queries (the spec validates only
# 1..99 inclusive) without committing to a high-resolution histogram that
# can't be stably re-fit from a noisy ~10k-bundle sample.
DEFAULT_PERCENTILES: tuple[int, ...] = (25, 50, 75, 90, 95, 99)


def _cohort_key_from_iterable(cohort: Iterable[str]) -> str:
    """Return the canonical comma-joined-sorted cohort key string.

    We use the comma-joined sorted form so the YAML key matches the Python
    ``frozenset`` lookup regardless of input ordering. This mirrors the
    BundleAuction's ``_lock_cohort_key`` discipline (sorted tuple of
    accounts).
    """
    items = sorted(str(a) for a in cohort if a)
    return ",".join(items)


def _empirical_percentile(sorted_values: Sequence[int], percentile: int) -> int:
    """Return the value at ``percentile`` of an already-sorted sequence.

    Uses the same nearest-rank discipline as ``BundleAuction.tip_quote``
    (``idx = (p * (n - 1)) // 100``) so a 100% calibrated weight yields
    *exactly* the same value as the auction's in-process percentile. This
    matters for the held-out test: any drift between fitter and auction
    rules shows up as systematic bias.
    """
    if not sorted_values:
        return 0
    n = len(sorted_values)
    idx = max(0, min(n - 1, (percentile * (n - 1)) // 100))
    return int(sorted_values[idx])


@dataclass(frozen=True, slots=True)
class _PercentileTable:
    """Compact percentile lookup table keyed by integer percentile."""

    points: tuple[tuple[int, int], ...]  # ((percentile, lamports), ...)
    n_bundles: int

    @classmethod
    def from_values(
        cls, values: Sequence[int], percentiles: Sequence[int] = DEFAULT_PERCENTILES
    ) -> "_PercentileTable":
        sorted_values = sorted(int(v) for v in values)
        if not sorted_values:
            return cls(points=(), n_bundles=0)
        points: list[tuple[int, int]] = []
        seen: set[int] = set()
        for p in percentiles:
            if p in seen:
                continue
            seen.add(p)
            points.append((int(p), _empirical_percentile(sorted_values, p)))
        points.sort(key=lambda x: x[0])
        return cls(points=tuple(points), n_bundles=len(sorted_values))

    def lookup(self, percentile: int) -> int | None:
        """Linear interpolation between stored breakpoints."""
        if not self.points or not 1 <= percentile <= 99:
            return None
        # Below the smallest stored breakpoint: return that point's value.
        if percentile <= self.points[0][0]:
            return self.points[0][1]
        # Above the largest stored breakpoint: return that point's value.
        if percentile >= self.points[-1][0]:
            return self.points[-1][1]
        for (p_lo, v_lo), (p_hi, v_hi) in zip(self.points, self.points[1:]):
            if p_lo <= percentile <= p_hi:
                if p_hi == p_lo:
                    return v_lo
                t = (percentile - p_lo) / (p_hi - p_lo)
                return int(round(v_lo + t * (v_hi - v_lo)))
        return None  # unreachable


@dataclass(frozen=True, slots=True)
class TipQuoteCurve:
    """Per-cohort + fallback empirical tip-quote prior.

    ``cohorts`` is keyed by the canonical comma-joined sorted cohort key
    (matches ``BundleAuction._lock_cohort_key``). The ``fallback`` table is
    fit on every captured bundle (in-cohort + out-of-cohort) and is used
    when a queried cohort has no entry or insufficient observations.

    The class is immutable; mutation is via a fresh ``fit_tip_quote_curve``
    or ``load_tip_quote_curve`` call.
    """

    captured_at: str
    n_bundles: int
    n_slots: int
    cohorts: Mapping[str, _PercentileTable]
    fallback: _PercentileTable
    source: str = "jito_tip_curves.yaml"
    schema_version: int = 1
    landing_rate: float | None = None
    landing_rate_method: str | None = None

    @property
    def n_in_cohort(self) -> int:
        return sum(t.n_bundles for t in self.cohorts.values())

    def percentile(self, percentile: int, cohort: Iterable[str] | None) -> int:
        """Return the calibrated tip percentile in lamports.

        Fallback rules (in order):
        1. Cohort match with ≥1 bundle observed → cohort table.
        2. Otherwise → population fallback.
        3. Empty calibration (0 bundles) → returns 0; callers must clamp to
           their floor.
        """
        if not 1 <= percentile <= 99:
            raise ValueError("percentile must be in [1, 99]")
        if cohort is not None:
            key = _cohort_key_from_iterable(cohort)
            table = self.cohorts.get(key)
            if table is not None and table.n_bundles > 0:
                v = table.lookup(percentile)
                if v is not None:
                    return v
        v = self.fallback.lookup(percentile)
        return v if v is not None else 0

    def cohort_n_bundles(self, cohort: Iterable[str]) -> int:
        key = _cohort_key_from_iterable(cohort)
        table = self.cohorts.get(key)
        return table.n_bundles if table is not None else 0

    # ── snapshot metadata for the JitoSearcher metrics block ──────────

    def metadata(self) -> dict[str, object]:
        """Compact provenance block for the ``calibration`` snapshot field."""
        meta: dict[str, object] = {
            "source": self.source,
            "captured_at": self.captured_at,
            "n_bundles": self.n_bundles,
            "n_slots": self.n_slots,
            "n_in_cohort": self.n_in_cohort,
        }
        if self.landing_rate is not None:
            meta["landing_rate"] = self.landing_rate
        if self.landing_rate_method is not None:
            meta["landing_rate_method"] = self.landing_rate_method
        return meta


# ── corpus reader ────────────────────────────────────────────────────────

def iter_bundle_rows(corpus_dir: Path) -> Iterator[dict]:
    """Yield bundle row dicts from ``corpus_dir/bundles.jsonl.gz``.

    Tolerates partial / interrupted writes by stopping at the first
    malformed line rather than raising — useful for fitting against an
    in-progress capture.
    """
    path = corpus_dir / "bundles.jsonl.gz"
    if not path.exists():
        return
    with gzip.open(path, "rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                return


# ── fitter ───────────────────────────────────────────────────────────────

def _bundle_in_cohort(row: Mapping[str, object], cohort_set: frozenset[str]) -> bool:
    """Re-derive cohort membership from the row's writable_accounts.

    The capture tool already sets ``is_in_cohort``, but the corpus may have
    been captured against a different cohort definition. Recomputing here
    lets the fitter slice the same corpus against multiple cohorts without
    re-capturing.
    """
    if not cohort_set:
        return bool(row.get("is_in_cohort"))
    writes = row.get("writable_accounts") or ()
    if not isinstance(writes, list):
        return False
    return any(w in cohort_set for w in writes)


def _approx_landing_rate(rows: Sequence[Mapping[str, object]]) -> tuple[float, str]:
    """Approximate Jito-relayer landing rate from a captured bundle corpus.

    The "ground truth" we'd want is: for every bundle the searcher sent to
    the Jito Block Engine, did it appear on chain in the requested slot?
    That telemetry isn't published. Approximation:

        landing_rate ≈ 1 - reverted_share

    Reasoning: bundles that *reach* a leader land or revert at the
    transaction level. The block-engine drop signal (rate-limited, no
    leader available) is invisible in finalized blocks. So this is an
    *upper bound* on the real landing rate — it conservatively assumes the
    block engine itself is a no-op gate. Documented in
    ``SubmissionPathPriors`` so the limitation is visible at the call site.
    """
    if not rows:
        return 0.0, "no-rows"
    reverted = sum(1 for r in rows if bool(r.get("any_tx_reverted")))
    n = len(rows)
    rate = 1.0 - (reverted / n)
    method = (
        "1 - reverted_share over captured bundles "
        "(upper bound; pre-leader drops are unobserved)"
    )
    return rate, method


def fit_tip_quote_curve(
    corpus_dir: Path,
    *,
    cohort: Sequence[str],
    percentiles: Sequence[int] = DEFAULT_PERCENTILES,
    captured_at: str | None = None,
    source: str = "jito_tip_curves.yaml",
    only_landed: bool = False,
    rows: Sequence[Mapping[str, object]] | None = None,
) -> TipQuoteCurve:
    """Fit per-cohort + fallback percentile tables from the captured corpus.

    Args:
        corpus_dir: directory containing ``bundles.jsonl.gz`` (ignored when
            ``rows`` is passed).
        cohort: cohort pubkey set (e.g. lighthouse SOL/USDC pool + vaults).
        percentiles: percentile breakpoints to store (default 25/50/75/90/
            95/99).
        captured_at: ISO-8601 timestamp recorded on the curve. Defaults to
            ``datetime.now(UTC)`` for fresh fits.
        only_landed: when True, drop bundles where ``any_tx_reverted=True``
            from the percentile fit. Default False — Jito tip transfers are
            ordered before the failing instruction in most bundles, so the
            tip lamports are still informative as bid signal.
        rows: pre-loaded rows (used by tests; production callers pass
            ``corpus_dir`` instead).

    Returns:
        ``TipQuoteCurve`` ready to persist via :func:`render_tip_quote_curve_yaml`.
    """
    if rows is None:
        rows = list(iter_bundle_rows(corpus_dir))
    cohort_set = frozenset(str(a) for a in cohort if a)
    cohort_key = _cohort_key_from_iterable(cohort_set)

    eligible_rows: list[Mapping[str, object]] = []
    for row in rows:
        try:
            tip = int(row.get("tip_lamports") or 0)
        except (TypeError, ValueError):
            continue
        if tip <= 0:
            continue
        if only_landed and bool(row.get("any_tx_reverted")):
            continue
        eligible_rows.append(row)

    fallback_table = _PercentileTable.from_values(
        [int(r.get("tip_lamports") or 0) for r in eligible_rows],
        percentiles,
    )
    cohort_tips = [
        int(r.get("tip_lamports") or 0)
        for r in eligible_rows
        if _bundle_in_cohort(r, cohort_set)
    ]
    cohort_tables: dict[str, _PercentileTable] = {}
    if cohort_key:
        cohort_tables[cohort_key] = _PercentileTable.from_values(
            cohort_tips, percentiles
        )

    n_slots = len({int(r.get("slot") or 0) for r in eligible_rows})
    landing_rate, landing_method = _approx_landing_rate(eligible_rows)

    return TipQuoteCurve(
        captured_at=captured_at
        or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        n_bundles=len(eligible_rows),
        n_slots=n_slots,
        cohorts=cohort_tables,
        fallback=fallback_table,
        source=source,
        landing_rate=landing_rate if eligible_rows else None,
        landing_rate_method=landing_method if eligible_rows else None,
    )


# ── persistence ──────────────────────────────────────────────────────────

def render_tip_quote_curve_yaml(curve: TipQuoteCurve) -> str:
    """Render ``curve`` as a stable, hand-readable YAML string.

    We hand-write the YAML to keep PyYAML out of the runtime dependency set
    (the rest of the engine writes manifest YAML the same way — see
    ``tools/cache_corpus_slot.py``). Stability matters for diffability:
    keys are emitted in a fixed order with stable formatting.
    """
    lines: list[str] = []
    lines.append(f"schema_version: {curve.schema_version}")
    lines.append(f'captured_at: "{curve.captured_at}"')
    lines.append(f"n_bundles: {curve.n_bundles}")
    lines.append(f"n_slots: {curve.n_slots}")
    if curve.landing_rate is not None:
        lines.append(f"landing_rate: {curve.landing_rate:.6f}")
    if curve.landing_rate_method is not None:
        lines.append(f'landing_rate_method: "{curve.landing_rate_method}"')
    lines.append("cohorts:")
    if not curve.cohorts:
        lines.append("  {}")
    else:
        for key in sorted(curve.cohorts.keys()):
            table = curve.cohorts[key]
            lines.append(f'  "{key}":')
            lines.append(f"    n_bundles: {table.n_bundles}")
            lines.append("    percentiles:")
            for p, v in table.points:
                lines.append(f"      {p}: {v}")
    lines.append("fallback:")
    lines.append(f"  n_bundles: {curve.fallback.n_bundles}")
    lines.append("  percentiles:")
    for p, v in curve.fallback.points:
        lines.append(f"    {p}: {v}")
    return "\n".join(lines) + "\n"


def write_tip_quote_curve_yaml(path: Path, curve: TipQuoteCurve) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tip_quote_curve_yaml(curve), encoding="utf-8")


def load_tip_quote_curve(path: Path) -> TipQuoteCurve:
    """Load a fitted curve from its YAML representation.

    We use the stdlib's ``json`` parser via a YAML-to-JSON shim so PyYAML
    isn't a runtime dep. The YAML we write is intentionally restricted to
    the simple-scalar subset that's also valid JSON-ish (no anchors, no
    flow lists for nested maps), making the conversion deterministic.
    Falls back to PyYAML when available so ad-hoc edits with comments
    still round-trip cleanly.
    """
    text = path.read_text(encoding="utf-8")
    data = _parse_curve_yaml(text)
    return _curve_from_dict(data, fallback_source=path.name)


def _parse_curve_yaml(text: str) -> dict:
    """Minimal hand-rolled parser for the curve YAML we emit.

    Schema: top-level scalars (strings / ints / floats), one nested
    ``cohorts:`` map (cohort key → {n_bundles, percentiles}), and one
    ``fallback:`` map. The parser tracks indentation depth in 2-space
    units. Unknown lines are skipped — keeps the loader robust to manual
    annotations in the committed YAML.
    """
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    out: dict = {}
    cohorts: dict = {}
    out["cohorts"] = cohorts
    fallback: dict = {}
    out["fallback"] = fallback

    # ``scope`` selects which sub-map subsequent indented lines populate.
    scope: dict | None = out
    cohort_key: str | None = None
    cohort_block: dict | None = None
    in_cohort_pcts = False
    in_fallback_pcts = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0:
            cohort_key = None
            cohort_block = None
            in_cohort_pcts = False
            in_fallback_pcts = False
            if stripped.startswith("cohorts:"):
                scope = cohorts
                continue
            if stripped.startswith("fallback:"):
                scope = fallback
                continue
            scope = out
            key, _, value = stripped.partition(":")
            value = value.strip()
            if value:
                out[key.strip()] = _coerce_scalar(value)
            continue

        if indent == 2 and scope is cohorts:
            # Either a cohort-key line ("\"...\":") or a fallback child.
            if stripped.endswith(":"):
                cohort_key = stripped[:-1].strip().strip('"')
                cohort_block = {"percentiles": {}}
                cohorts[cohort_key] = cohort_block
                in_cohort_pcts = False
                continue
        if indent == 2 and scope is fallback:
            if stripped.startswith("percentiles:"):
                in_fallback_pcts = True
                fallback["percentiles"] = {}
                continue
            key, _, value = stripped.partition(":")
            fallback[key.strip()] = _coerce_scalar(value.strip())
            continue
        if indent == 4 and scope is cohorts and cohort_block is not None:
            if stripped.startswith("percentiles:"):
                in_cohort_pcts = True
                continue
            in_cohort_pcts = False
            key, _, value = stripped.partition(":")
            cohort_block[key.strip()] = _coerce_scalar(value.strip())
            continue
        if indent == 6 and scope is cohorts and cohort_block is not None and in_cohort_pcts:
            key, _, value = stripped.partition(":")
            cohort_block["percentiles"][int(key.strip())] = int(value.strip())
            continue
        if indent == 4 and scope is fallback and in_fallback_pcts:
            key, _, value = stripped.partition(":")
            fallback["percentiles"][int(key.strip())] = int(value.strip())
            continue
    return out


def _coerce_scalar(value: str) -> object:
    if not value:
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _curve_from_dict(data: Mapping[str, object], *, fallback_source: str) -> TipQuoteCurve:
    cohorts: dict[str, _PercentileTable] = {}
    raw_cohorts = data.get("cohorts") or {}
    if isinstance(raw_cohorts, Mapping):
        for key, body in raw_cohorts.items():
            if not isinstance(body, Mapping):
                continue
            pcts = body.get("percentiles") or {}
            if not isinstance(pcts, Mapping):
                continue
            points = tuple(
                sorted((int(p), int(v)) for p, v in pcts.items())
            )
            cohorts[str(key)] = _PercentileTable(
                points=points,
                n_bundles=int(body.get("n_bundles") or 0),
            )
    fallback_raw = data.get("fallback") or {}
    pcts = (
        fallback_raw.get("percentiles")
        if isinstance(fallback_raw, Mapping)
        else None
    ) or {}
    fallback_points = tuple(
        sorted((int(p), int(v)) for p, v in pcts.items())
    )
    fallback = _PercentileTable(
        points=fallback_points,
        n_bundles=int(fallback_raw.get("n_bundles") if isinstance(fallback_raw, Mapping) else 0)
        if isinstance(fallback_raw, Mapping)
        else 0,
    )
    landing_rate = data.get("landing_rate")
    landing_method = data.get("landing_rate_method")
    return TipQuoteCurve(
        captured_at=str(data.get("captured_at") or ""),
        n_bundles=int(data.get("n_bundles") or 0),
        n_slots=int(data.get("n_slots") or 0),
        cohorts=cohorts,
        fallback=fallback,
        source=str(data.get("source") or fallback_source),
        schema_version=int(data.get("schema_version") or 1),
        landing_rate=float(landing_rate) if isinstance(landing_rate, (int, float)) else None,
        landing_rate_method=str(landing_method) if isinstance(landing_method, str) else None,
    )
