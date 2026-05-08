"""Replay+bundle metric tests (PRD US-006 / line 995)."""

from __future__ import annotations

from defi_sim.core.types import BundleOutcome
from defi_sim.metrics.replay import (
    compute_bundle_landing_rate,
    compute_cu_per_dollar_tip_breakeven_curve,
    compute_skip_rate_cost,
    compute_slot_inclusion_latency,
    compute_submission_path_comparison,
    compute_tip_efficiency,
    compute_write_lock_heatmap,
)


def _outcome(status: str, slot: int = 1, idx: int = 0) -> BundleOutcome:
    return BundleOutcome(
        slot=slot,
        bundle_index=idx,
        status=status,  # type: ignore[arg-type]
        tip_lamports=0,
        validator_revenue_lamports=0,
        stake_pool_revenue_lamports=0,
    )


def test_bundle_landing_rate_matches_expected() -> None:
    """PRD line 997: 2 of 5 bundles land → landing rate == 0.4."""
    outcomes = [
        _outcome("landed", idx=0),
        _outcome("landed", idx=1),
        _outcome("dropped", idx=2),
        _outcome("reverted", idx=3),
        _outcome("dropped", idx=4),
    ]

    result = compute_bundle_landing_rate(outcomes)

    assert result.name == "bundle_landing_rate"
    assert result.unit == "ratio"
    assert result.sample_size == 5
    assert result.value == 0.4


def test_bundle_landing_rate_empty_returns_zero() -> None:
    result = compute_bundle_landing_rate([])
    assert result.value == 0.0
    assert result.sample_size == 0


def test_tip_efficiency_zero_when_no_extraction() -> None:
    """PRD line 998: tips paid with zero extraction → efficiency = 0.0.

    Three landed bundles each paid a 5_000-lamport tip but extracted nothing
    (e.g. failed arbs that still tipped, or non-extractive bundles). Total
    extracted value is 0, so the metric returns the 0.0 sentinel rather than
    dividing by zero.
    """
    samples = [
        (5_000, 0),
        (5_000, 0),
        (5_000, 0),
    ]

    result = compute_tip_efficiency(samples)

    assert result.name == "tip_efficiency"
    assert result.unit == "ratio"
    assert result.sample_size == 3
    assert result.value == 0.0


def test_tip_efficiency_empty_returns_zero() -> None:
    result = compute_tip_efficiency([])
    assert result.value == 0.0
    assert result.sample_size == 0


def test_tip_efficiency_aggregates_across_bundles() -> None:
    """Sanity: sum(tip)/sum(ev) — 1_000 + 2_000 over 5_000 + 5_000 = 0.3."""
    samples = [(1_000, 5_000), (2_000, 5_000)]
    result = compute_tip_efficiency(samples)
    assert result.sample_size == 2
    assert result.value == 0.3


def test_slot_inclusion_latency_distribution_well_formed() -> None:
    """PRD line 999: latency distribution exposes monotone percentiles.

    "Well-formed" here means: name/unit are correct, sample_size matches the
    number of valid (non-negative-latency) pairs, the percentile ladder is
    monotone non-decreasing (median <= p95 <= p99), and mean is bounded by
    the observed min/max. The headline scalar shadows the median into the
    canonical MetricResult shape.
    """
    samples = [
        (100, 100),  # latency 0 — landed in same slot
        (101, 102),  # latency 1
        (102, 104),  # latency 2
        (103, 106),  # latency 3
        (104, 108),  # latency 4
        (105, 110),  # latency 5
        (106, 113),  # latency 7
        (107, 117),  # latency 10
        (108, 123),  # latency 15
        (109, 129),  # latency 20
    ]

    dist = compute_slot_inclusion_latency(samples)

    assert dist.name == "slot_inclusion_latency"
    assert dist.unit == "slots"
    assert dist.sample_size == len(samples)
    assert dist.samples == (0, 1, 2, 3, 4, 5, 7, 10, 15, 20)

    assert dist.median <= dist.p95 <= dist.p99
    assert min(dist.samples) <= dist.mean <= max(dist.samples)
    assert dist.mean == sum(dist.samples) / len(dist.samples)

    headline = dist.headline
    assert headline.name == "slot_inclusion_latency"
    assert headline.unit == "slots"
    assert headline.sample_size == len(samples)
    assert headline.value == dist.median


def test_slot_inclusion_latency_empty_returns_zero() -> None:
    dist = compute_slot_inclusion_latency([])
    assert dist.sample_size == 0
    assert dist.mean == 0.0
    assert dist.median == 0.0
    assert dist.p95 == 0.0
    assert dist.p99 == 0.0
    assert dist.samples == ()


def test_slot_inclusion_latency_drops_inverted_pairs() -> None:
    """A landed_slot < submitted_slot is a corrupt sample, not a bundle that
    landed. Drop it from the distribution rather than reporting negative
    latency."""
    samples = [(100, 100), (101, 100), (102, 105)]  # second pair is inverted
    dist = compute_slot_inclusion_latency(samples)
    assert dist.sample_size == 2
    assert dist.samples == (0, 3)


def test_cu_per_dollar_tip_breakeven_curve_monotonic() -> None:
    """PRD line 1000: break-even scatter curve is monotonic on tip axis.

    Inputs are ``(tip_lamports, extracted_value_lamports)`` per landed bundle.
    The calculator returns the curve sorted ascending by tip — a load-bearing
    invariant of the scatter (PRD line 984), since the chart layer treats tip
    as the x-axis. Ratios are paired to the same sort order so the chart can
    annotate "above/below break-even" without re-zipping. The headline scalar
    is the fraction of landed bundles that cleared ``ev >= tip``.
    """
    samples = [
        (5_000, 4_000),    # below break-even, ratio 0.8
        (1_000, 5_000),    # above, ratio 5.0
        (10_000, 10_000),  # exactly break-even, ratio 1.0
        (3_000, 6_000),    # above, ratio 2.0
        (20_000, 5_000),   # below, ratio 0.25
        (7_500, 9_000),    # above, ratio 1.2
    ]

    curve = compute_cu_per_dollar_tip_breakeven_curve(samples)

    assert curve.name == "cu_per_dollar_tip_breakeven"
    assert curve.unit == "lamports"
    assert curve.sample_size == len(samples)

    assert curve.tips == (1_000, 3_000, 5_000, 7_500, 10_000, 20_000)
    assert curve.extracted_values == (5_000, 6_000, 4_000, 9_000, 10_000, 5_000)
    assert all(curve.tips[i] <= curve.tips[i + 1] for i in range(len(curve.tips) - 1))

    expected_ratios = (5.0, 2.0, 0.8, 1.2, 1.0, 0.25)
    assert curve.ratios == expected_ratios

    headline = curve.headline
    assert headline.name == "cu_per_dollar_tip_breakeven"
    assert headline.unit == "ratio"
    assert headline.sample_size == len(samples)
    assert headline.value == 4 / 6


def test_cu_per_dollar_tip_breakeven_curve_empty_returns_empty() -> None:
    curve = compute_cu_per_dollar_tip_breakeven_curve([])
    assert curve.sample_size == 0
    assert curve.tips == ()
    assert curve.extracted_values == ()
    assert curve.ratios == ()
    assert curve.headline.value == 0.0


def test_cu_per_dollar_tip_breakeven_curve_zero_tip_ratio_is_zero() -> None:
    """A zero-tip landed bundle is well-formed input but has no defined ratio.

    Returning the 0.0 sentinel — same convention as ``tip_efficiency`` — keeps
    the chart layer free of NaN/inf branching."""
    curve = compute_cu_per_dollar_tip_breakeven_curve([(0, 1_000)])
    assert curve.ratios == (0.0,)


def test_skip_rate_cost_zero_with_zero_skip() -> None:
    """PRD line 1001: zero skipped slots → zero lost EV.

    Five slots in the window, none skipped — every slot contributes 0 to the
    cost regardless of its EV. ``sample_size`` reports the full window so the
    chart layer can render "0 cost over N slots" instead of "no data".
    """
    samples = [
        (False, 10_000),
        (False, 5_000),
        (False, 25_000),
        (False, 0),
        (False, 8_000),
    ]

    result = compute_skip_rate_cost(samples)

    assert result.name == "skip_rate_cost"
    assert result.unit == "lamports"
    assert result.sample_size == 5
    assert result.value == 0.0


def test_skip_rate_cost_empty_returns_zero() -> None:
    result = compute_skip_rate_cost([])
    assert result.value == 0.0
    assert result.sample_size == 0


def test_skip_rate_cost_sums_only_skipped_slot_ev() -> None:
    """Sanity: skipped slots' EV is summed; non-skipped contribute nothing."""
    samples = [
        (True, 12_000),
        (False, 50_000),
        (True, 8_000),
        (False, 7_500),
    ]
    result = compute_skip_rate_cost(samples)
    assert result.sample_size == 4
    assert result.value == 20_000.0


def test_write_lock_heatmap_correctly_aggregates() -> None:
    """PRD line 1002: write-lock claims aggregate to a (account, slot) grid.

    Six write-lock claims across three accounts and two slots. Repeated
    (account, slot) pairs stack — that is the contention signal. Output axes
    are sorted (accounts lexicographic, slots numeric) so the chart renders
    a stable grid. The headline scalar is the maximum single-cell count, the
    "worst contention" projected into the canonical MetricResult shape.
    """
    claims = [
        ("orca_pool", 100),
        ("orca_pool", 100),  # same cell stacks
        ("orca_pool", 101),
        ("raydium_pool", 100),
        ("raydium_pool", 101),
        ("raydium_pool", 101),  # same cell stacks
        ("raydium_pool", 101),  # again — this cell is the max
        ("marginfi_bank", 101),
    ]

    heatmap = compute_write_lock_heatmap(claims)

    assert heatmap.name == "write_lock_heatmap"
    assert heatmap.unit == "locks"
    assert heatmap.sample_size == len(claims)
    assert heatmap.accounts == ("marginfi_bank", "orca_pool", "raydium_pool")
    assert heatmap.slots == (100, 101)
    assert heatmap.counts == {
        ("orca_pool", 100): 2,
        ("orca_pool", 101): 1,
        ("raydium_pool", 100): 1,
        ("raydium_pool", 101): 3,
        ("marginfi_bank", 101): 1,
    }
    assert heatmap.max_contention == 3

    headline = heatmap.headline
    assert headline.name == "write_lock_heatmap"
    assert headline.unit == "locks"
    assert headline.sample_size == len(claims)
    assert headline.value == 3.0


def test_write_lock_heatmap_empty_returns_empty() -> None:
    heatmap = compute_write_lock_heatmap([])
    assert heatmap.sample_size == 0
    assert heatmap.accounts == ()
    assert heatmap.slots == ()
    assert heatmap.counts == {}
    assert heatmap.max_contention == 0
    assert heatmap.headline.value == 0.0


def test_submission_path_comparison_three_paths() -> None:
    """PRD line 1003: per-path landing rates compared across three paths.

    Ten submission attempts spread across three paths — Jito relay, direct
    leader gRPC, and public RPC. Each path's landing rate is ``landed /
    submitted``; the headline scalar is the spread between the best and
    worst path, the load-bearing signal for "is your submission path
    costing you bundles?".
    """
    samples = [
        ("jito_relay", True),
        ("jito_relay", True),
        ("jito_relay", True),
        ("jito_relay", False),
        ("direct_leader", True),
        ("direct_leader", True),
        ("direct_leader", True),
        ("public_rpc", False),
        ("public_rpc", True),
        ("public_rpc", False),
    ]

    comparison = compute_submission_path_comparison(samples)

    assert comparison.name == "submission_path_comparison"
    assert comparison.unit == "ratio"
    assert comparison.sample_size == len(samples)
    assert comparison.paths == ("direct_leader", "jito_relay", "public_rpc")
    assert comparison.submitted == (3, 4, 3)
    assert comparison.landed == (3, 3, 1)
    assert comparison.landing_rates == (1.0, 0.75, 1 / 3)
    assert comparison.spread == 1.0 - (1 / 3)

    headline = comparison.headline
    assert headline.name == "submission_path_comparison"
    assert headline.unit == "ratio"
    assert headline.sample_size == len(samples)
    assert headline.value == comparison.spread


def test_submission_path_comparison_empty_returns_empty() -> None:
    comparison = compute_submission_path_comparison([])
    assert comparison.sample_size == 0
    assert comparison.paths == ()
    assert comparison.submitted == ()
    assert comparison.landed == ()
    assert comparison.landing_rates == ()
    assert comparison.spread == 0.0
    assert comparison.headline.value == 0.0


def test_submission_path_comparison_single_path_zero_spread() -> None:
    """One path means no spread — best == worst, so the headline is 0.0."""
    samples = [("jito_relay", True), ("jito_relay", False)]
    comparison = compute_submission_path_comparison(samples)
    assert comparison.paths == ("jito_relay",)
    assert comparison.landing_rates == (0.5,)
    assert comparison.spread == 0.0
