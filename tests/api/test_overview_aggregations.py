"""Unit tests for ``defi_sim_api.backend.overview_aggregations``.

Aggregations are pure functions over the per-round summaries plucked by
``query_overview_result_slices``. We test them in isolation here so the
integration tests in ``test_run_overview_view.py`` can stay focused on the
wire-shape contract rather than aggregation arithmetic.
"""

from __future__ import annotations

from defi_sim_api.backend.overview_aggregations import (
    aggregate_bundle_outcomes_summary,
    aggregate_jito_searcher_summary,
    aggregate_solana_slot_summary,
    latest_replay_metrics,
)


# ──────────────────────────────────────────────────────────────────────
# Solana slot summary
# ──────────────────────────────────────────────────────────────────────


def test_solana_slot_summary_returns_last_non_null():
    snaps = [
        {"current_slot": 100, "current_leader": "alice"},
        {"current_slot": 101, "current_leader": None},
        {"current_slot": 102, "current_leader": "bob"},
    ]
    assert aggregate_solana_slot_summary(snaps) == {
        "current_slot": 102,
        "current_leader": "bob",
    }


def test_solana_slot_summary_carries_last_known_leader_through_gaps():
    """Mirrors the page memo: each field independently sticks to its last
    non-null value, so a slot update without a leader doesn't blank the
    leader display.
    """
    snaps = [
        {"current_slot": 100, "current_leader": "alice"},
        {"current_slot": 101},  # no leader key
    ]
    assert aggregate_solana_slot_summary(snaps) == {
        "current_slot": 101,
        "current_leader": "alice",
    }


def test_solana_slot_summary_returns_none_for_non_solana_run():
    snaps = [{"current_slot": None, "current_leader": None}]
    assert aggregate_solana_slot_summary(snaps) is None


def test_solana_slot_summary_returns_none_for_empty_input():
    assert aggregate_solana_slot_summary([]) is None
    assert aggregate_solana_slot_summary(None) is None


# ──────────────────────────────────────────────────────────────────────
# Bundle outcomes summary
# ──────────────────────────────────────────────────────────────────────


def test_bundle_outcomes_aggregates_counts_and_timeline():
    snaps = [
        {
            "bundle_outcomes": [
                {"status": "landed", "validator_revenue_lamports": 100, "stake_pool_revenue_lamports": 50},
                {"status": "reverted"},
            ]
        },
        {
            "bundle_outcomes": [
                {"status": "landed", "validator_revenue_lamports": 200, "stake_pool_revenue_lamports": 0},
                {"status": "dropped", "drop_reason": "slot_skipped"},
                {"status": "dropped", "drop_reason": "slot_skipped"},
                {"status": "dropped"},  # unknown reason
            ]
        },
    ]
    summary = aggregate_bundle_outcomes_summary(snaps)
    assert summary is not None
    assert summary["counts"] == {"landed": 2, "reverted": 1, "dropped": 3}
    assert summary["timeline"] == {
        "landed": [1, 1],
        "reverted": [1, 0],
        "dropped": [0, 3],
    }
    assert summary["tips_paid_lamports"] == 350.0
    assert summary["drop_reasons"] == {"slot_skipped": 2, "unknown": 1}


def test_bundle_outcomes_landing_rate_stats_match_perround_average():
    """Per-round landing rate is computed only over rounds that had at least
    one outcome; landing_rate_stats then averages those. Mirrors the page
    IIFE's ``perRoundLandingRates`` math exactly.
    """
    snaps = [
        # Round 0: 1 landed, 1 dropped → landing rate 0.5
        {"bundle_outcomes": [{"status": "landed"}, {"status": "dropped"}]},
        # Round 1: no bundles → excluded from average
        {"bundle_outcomes": []},
        # Round 2: 1 landed, 0 others → landing rate 1.0
        {"bundle_outcomes": [{"status": "landed"}]},
    ]
    summary = aggregate_bundle_outcomes_summary(snaps)
    assert summary is not None
    stats = summary["landing_rate_stats"]
    assert stats["rounds_with_bundles"] == 2
    assert abs(stats["avg"] - 0.75) < 1e-12
    # stdev over [0.5, 1.0]: variance = 0.0625, stdev = 0.25
    assert abs(stats["stdev"] - 0.25) < 1e-12


def test_bundle_outcomes_returns_none_when_no_round_had_outcomes():
    """Empty ``bundle_outcomes`` arrays on every round means this is not a
    Jito-bundle-aware run; the page should hide the bundle counters entirely.
    """
    snaps = [{"bundle_outcomes": []}, {"bundle_outcomes": []}]
    assert aggregate_bundle_outcomes_summary(snaps) is None


def test_bundle_outcomes_ignores_non_finite_revenue():
    """Page guards with ``Number.isFinite`` before summing; the server must
    match so a stray NaN/Inf in one outcome doesn't poison ``tips_paid``."""
    snaps = [
        {
            "bundle_outcomes": [
                {"status": "landed", "validator_revenue_lamports": float("inf"), "stake_pool_revenue_lamports": 100},
                {"status": "landed", "validator_revenue_lamports": float("nan"), "stake_pool_revenue_lamports": 50},
            ]
        }
    ]
    summary = aggregate_bundle_outcomes_summary(snaps)
    assert summary is not None
    assert summary["tips_paid_lamports"] == 150.0


# ──────────────────────────────────────────────────────────────────────
# Jito-searcher summary
# ──────────────────────────────────────────────────────────────────────


def test_jito_searcher_summary_sums_strategy_counters_on_final_snapshot():
    snaps = [
        # Earlier snapshot — ignored (only the final snapshot is canonical).
        {"jito_searcher": {"searcher-1": {"by_strategy": {"s": {"bundles_submitted": 5}}}}},
        {
            "jito_searcher": {
                "searcher-1": {
                    "by_strategy": {
                        "front": {
                            "bundles_submitted": 10,
                            "bundles_landed": 4,
                            "tips_submitted_lamports": 1000,
                            "tips_paid_lamports": 400,
                            "realized_ev_lamports": 800,
                        },
                        "back": {
                            "bundles_submitted": 5,
                            "bundles_landed": 1,
                            "tips_submitted_lamports": 200,
                            "tips_paid_lamports": 100,
                            "realized_ev_lamports": 50,
                        },
                    }
                }
            }
        },
    ]
    summary = aggregate_jito_searcher_summary(snaps)
    assert summary is not None
    assert summary["bundles_submitted"] == 15
    assert summary["bundles_landed"] == 5
    assert summary["tips_submitted_lamports"] == 1200
    assert summary["tips_paid_lamports"] == 500
    assert summary["realized_ev_lamports"] == 850
    # landing_rate = 5/15, tip_roi = 850/500
    assert abs(summary["landing_rate"] - 5 / 15) < 1e-12
    assert abs(summary["tip_roi"] - 850 / 500) < 1e-12


def test_jito_searcher_summary_carries_synthetic_and_calibration_flags():
    snaps = [
        {
            "jito_searcher": {
                "searcher-1": {
                    "synthetic": True,
                    "calibration": {"source": "fitted", "n_bundles": 42},
                    "by_strategy": {"front": {"bundles_submitted": 1, "bundles_landed": 0}},
                }
            }
        }
    ]
    summary = aggregate_jito_searcher_summary(snaps)
    assert summary is not None
    assert summary["synthetic"] is True
    assert summary["calibration"] == {"source": "fitted", "n_bundles": 42}


def test_jito_searcher_summary_returns_none_when_strategy_count_is_zero():
    """Empty by_strategy means no searcher fired — summary suppresses itself."""
    snaps = [{"jito_searcher": {"searcher-1": {"by_strategy": {}}}}]
    assert aggregate_jito_searcher_summary(snaps) is None


def test_jito_searcher_summary_returns_none_when_field_missing():
    snaps = [{"jito_searcher": None}]
    assert aggregate_jito_searcher_summary(snaps) is None


def test_jito_searcher_summary_landing_rate_zero_when_no_bundles_submitted():
    """Divide-by-zero guards must match the page's ``a > 0 ? a/b : 0``."""
    snaps = [
        {
            "jito_searcher": {
                "searcher-1": {
                    "by_strategy": {
                        "front": {
                            "bundles_submitted": 0,
                            "bundles_landed": 0,
                            "tips_paid_lamports": 0,
                            "realized_ev_lamports": 0,
                        }
                    }
                }
            }
        }
    ]
    summary = aggregate_jito_searcher_summary(snaps)
    assert summary is not None
    assert summary["landing_rate"] == 0.0
    assert summary["tip_roi"] == 0.0


# ──────────────────────────────────────────────────────────────────────
# Replay metrics
# ──────────────────────────────────────────────────────────────────────


def test_latest_replay_metrics_picks_last_non_null():
    snaps = [
        {"replay": {"step": 0}},
        {"replay": None},
        {"replay": {"step": 5, "matched": True}},
    ]
    assert latest_replay_metrics(snaps) == {"step": 5, "matched": True}


def test_latest_replay_metrics_returns_none_when_never_present():
    assert latest_replay_metrics([{"replay": None}, {}]) is None
    assert latest_replay_metrics([]) is None
    assert latest_replay_metrics(None) is None
