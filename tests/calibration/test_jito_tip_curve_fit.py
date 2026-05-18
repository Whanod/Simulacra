"""Fitter unit tests for the Jito tip-quote curve (FIX-020).

These tests run against a small synthetic corpus generated in-process so
they're hermetic — no RPC calls, no committed fixture dependency. The
held-out-slot regression test in ``test_jito_tip_curve_holdout.py`` runs
against the real captured corpus and skips when it isn't present.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from defi_sim_solana.calibration import (
    TipQuoteCurve,
    fit_tip_quote_curve,
    load_tip_quote_curve,
)
from defi_sim_solana.calibration.tip_quote import (
    DEFAULT_PERCENTILES,
    render_tip_quote_curve_yaml,
    write_tip_quote_curve_yaml,
)

pytestmark = pytest.mark.calibration


COHORT = (
    "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
    "EUuUbDcafPrmVTD5M6qoJAoyyNbihBhugADAxRMn5he9",
)


def _write_corpus(tmp_path: Path, rows: list[dict]) -> Path:
    out = tmp_path / "bundles.jsonl.gz"
    with gzip.open(out, "wb") as fh:
        for r in rows:
            fh.write(json.dumps(r).encode("utf-8"))
            fh.write(b"\n")
    return tmp_path


def test_fit_emits_default_percentile_breakpoints(tmp_path: Path) -> None:
    """A 100-row uniform sample produces all six breakpoints."""
    rows = [
        {
            "slot": 1_000_000 + i,
            "tip_lamports": (i + 1) * 1000,  # 1000, 2000, ..., 100_000
            "writable_accounts": [],
            "is_in_cohort": False,
            "any_tx_reverted": False,
        }
        for i in range(100)
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT, captured_at="2026-05-05T00:00:00+00:00")

    assert curve.n_bundles == 100
    fallback_pcts = {p: v for p, v in curve.fallback.points}
    assert sorted(fallback_pcts.keys()) == list(DEFAULT_PERCENTILES)
    # Nearest-rank percentile on a 100-row uniform sample over 1000..100000:
    # idx = (p * 99) // 100, value = (idx + 1) * 1000.
    assert fallback_pcts[50] == 50_000  # idx=49 → 50000
    assert fallback_pcts[90] == 90_000  # idx=89 → 90000
    assert fallback_pcts[99] == 99_000  # idx=98 → 99000


def test_fit_separates_cohort_and_fallback(tmp_path: Path) -> None:
    """Cohort tips should populate the cohort table, not just the fallback."""
    cohort_tips = [10_000] * 50  # tight cohort distribution
    fallback_tips = [1_000] * 200  # loose population
    rows = [
        {
            "slot": 1_000_000 + i,
            "tip_lamports": v,
            "writable_accounts": [COHORT[0]],
            "is_in_cohort": True,
            "any_tx_reverted": False,
        }
        for i, v in enumerate(cohort_tips)
    ] + [
        {
            "slot": 2_000_000 + i,
            "tip_lamports": v,
            "writable_accounts": [],
            "is_in_cohort": False,
            "any_tx_reverted": False,
        }
        for i, v in enumerate(fallback_tips)
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT)

    cohort_n = curve.cohort_n_bundles(COHORT)
    assert cohort_n == 50
    assert curve.percentile(50, COHORT) == 10_000
    assert curve.percentile(50, None) in (1_000, 10_000)
    # Out-of-cohort lookup falls back to population
    other_cohort = ("UnknownAccount111111111111111111111111111",)
    assert curve.percentile(50, other_cohort) == curve.fallback.lookup(50)


def test_fit_filters_zero_tip_rows(tmp_path: Path) -> None:
    """Bundles with non-positive tips are dropped from the percentile fit."""
    rows = [
        {"slot": i, "tip_lamports": 0, "writable_accounts": [], "is_in_cohort": False}
        for i in range(10)
    ] + [
        {"slot": 100 + i, "tip_lamports": 5_000, "writable_accounts": [], "is_in_cohort": False}
        for i in range(5)
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT)
    assert curve.n_bundles == 5


def test_only_landed_filter_drops_reverted(tmp_path: Path) -> None:
    rows = [
        {
            "slot": i,
            "tip_lamports": 10_000,
            "writable_accounts": [],
            "is_in_cohort": False,
            "any_tx_reverted": (i % 2 == 0),
        }
        for i in range(100)
    ]
    _write_corpus(tmp_path, rows)
    curve_all = fit_tip_quote_curve(tmp_path, cohort=COHORT, only_landed=False)
    curve_landed = fit_tip_quote_curve(tmp_path, cohort=COHORT, only_landed=True)
    assert curve_all.n_bundles == 100
    assert curve_landed.n_bundles == 50


def test_landing_rate_proxy_within_tolerance(tmp_path: Path) -> None:
    """1 - reverted_share is the documented landing-rate approximation."""
    rows = [
        {
            "slot": i,
            "tip_lamports": 10_000,
            "writable_accounts": [],
            "is_in_cohort": False,
            "any_tx_reverted": (i < 20),
        }
        for i in range(100)
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT)
    assert curve.landing_rate is not None
    assert abs(curve.landing_rate - 0.80) < 1e-6
    assert curve.landing_rate_method
    assert "upper bound" in curve.landing_rate_method


def test_yaml_roundtrip_preserves_percentiles(tmp_path: Path) -> None:
    rows = [
        {
            "slot": i,
            "tip_lamports": (i + 1) * 100,
            "writable_accounts": [COHORT[0]] if i % 3 == 0 else [],
            "is_in_cohort": (i % 3 == 0),
            "any_tx_reverted": False,
        }
        for i in range(60)
    ]
    _write_corpus(tmp_path, rows)
    original = fit_tip_quote_curve(tmp_path, cohort=COHORT)
    yaml_path = tmp_path / "curve.yaml"
    write_tip_quote_curve_yaml(yaml_path, original)
    loaded = load_tip_quote_curve(yaml_path)

    assert loaded.n_bundles == original.n_bundles
    assert loaded.captured_at == original.captured_at
    for p in DEFAULT_PERCENTILES:
        assert loaded.percentile(p, COHORT) == original.percentile(p, COHORT)
        assert loaded.percentile(p, None) == original.percentile(p, None)


def test_percentile_lookup_interpolates_between_breakpoints(tmp_path: Path) -> None:
    """Queries between stored percentiles linearly interpolate."""
    rows = [
        {
            "slot": i,
            "tip_lamports": v,
            "writable_accounts": [],
            "is_in_cohort": False,
        }
        for i, v in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000])
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT)
    p25 = curve.fallback.lookup(25)
    p50 = curve.fallback.lookup(50)
    # Mid-range percentile must lie between adjacent breakpoints (inclusive).
    p37 = curve.fallback.lookup(37)
    assert p25 is not None and p50 is not None and p37 is not None
    assert min(p25, p50) <= p37 <= max(p25, p50)


def test_empty_corpus_returns_zero_bundles(tmp_path: Path) -> None:
    _write_corpus(tmp_path, [])
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT)
    assert curve.n_bundles == 0
    assert curve.percentile(50, COHORT) == 0
    assert curve.landing_rate is None


def test_metadata_block_for_snapshot(tmp_path: Path) -> None:
    rows = [
        {
            "slot": i,
            "tip_lamports": 10_000,
            "writable_accounts": [],
            "is_in_cohort": False,
            "any_tx_reverted": False,
        }
        for i in range(10)
    ]
    _write_corpus(tmp_path, rows)
    curve = fit_tip_quote_curve(tmp_path, cohort=COHORT, captured_at="2026-05-05T00:00:00+00:00")
    meta = curve.metadata()
    assert meta["source"] == "jito_tip_curves.yaml"
    assert meta["captured_at"] == "2026-05-05T00:00:00+00:00"
    assert meta["n_bundles"] == 10
    assert meta["n_slots"] == 10
    assert "landing_rate" in meta
