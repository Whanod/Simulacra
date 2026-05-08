"""Tests for the ``JitoSearcher`` agent (PRD US-013 line 1000)."""

from __future__ import annotations

import pytest

from defi_sim.agents.jito_searcher import (
    JitoSearcher,
    JitoSearcherMetrics,
    JitoSearcherParams,
)
from defi_sim.agents.tip_curve import TipCurveSpec
from defi_sim.core.agent import DecisionContext
from defi_sim.core.types import SwapAction
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS


def _params(strategies: list[str]) -> JitoSearcherParams:
    return JitoSearcherParams(
        strategies=strategies,  # type: ignore[arg-type]
        tip_curve=TipCurveSpec(kind="linear"),
        min_ev_to_submit_lamports=10_000,
        tip_account="tip-account",
    )


def test_first_class_strategies_construct_without_error() -> None:
    params = _params(["backrun", "sandwich"])
    assert params.strategies == ["backrun", "sandwich"]


def test_jit_lp_strategy_raises_unsupported_at_construction() -> None:
    with pytest.raises(ValueError, match="jit_lp.*3.1.3a"):
        _params(["jit_lp"])


def test_liquidation_strategy_raises_unsupported_at_construction() -> None:
    with pytest.raises(ValueError, match="liquidation.*3.2.1a"):
        _params(["liquidation"])


def test_mixed_strategy_list_raises_on_first_deferred_entry() -> None:
    with pytest.raises(ValueError, match="jit_lp"):
        _params(["backrun", "jit_lp"])


def test_searcher_constructs_with_first_class_strategies() -> None:
    params = _params(["backrun"])
    agent = JitoSearcher(agent_id="searcher-1", params=params)
    assert agent.agent_id == "searcher-1"
    assert agent.state.role.name == "jito_searcher"


def test_backrun_strategy_emits_bundle_on_large_swap() -> None:
    """PRD US-013 line 1061: detect large pending swap, emit back-run bundle."""
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    victim = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=100_000)
    ctx = DecisionContext(pending_actions=[victim])

    bundle = agent.run_backrun(ctx)

    assert bundle is not None
    assert bundle.execute_after_regular_actions is True
    # Back-run swap goes the opposite direction on the same pair.
    backrun_swap = bundle.txs[0].actions[0]
    assert isinstance(backrun_swap, SwapAction)
    assert backrun_swap.token_in == "SOL"
    assert backrun_swap.token_out == "USDC"
    # Tip clears Jito's protocol minimum.
    assert bundle.tip_lamports >= MIN_BUNDLE_TIP_LAMPORTS
    # Tip is paid to the configured tip account.
    assert bundle.tip_payments[0].recipient == "tip-account"


def test_backrun_validation_tip_equals_5pct_of_ev() -> None:
    """PRD US-013 line 1056: linear tip curve at 5% of EV → tip = 5% of EV.

    Validation criterion: a ``JitoSearcher`` with ``strategies=["backrun"]``
    and a ``linear`` tip curve detects a large noise-trader swap and emits a
    back-run bundle whose tip is the curve's apply-result (5% of EV at the
    default 0.05 slope). EV is proxied by the victim's ``amount_in``.
    """
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    victim = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=100_000)
    ctx = DecisionContext(pending_actions=[victim])

    bundle = agent.run_backrun(ctx)

    assert bundle is not None
    assert bundle.tip_lamports == 5_000


def test_backrun_strategy_skips_swap_below_min_ev_threshold() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    small_swap = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=1_000)
    ctx = DecisionContext(pending_actions=[small_swap])

    assert agent.run_backrun(ctx) is None


def test_backrun_strategy_returns_none_without_pending_actions() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    assert agent.run_backrun(DecisionContext()) is None


def test_backrun_strategy_returns_none_when_strategy_not_configured() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["sandwich"]))
    victim = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=100_000)
    ctx = DecisionContext(pending_actions=[victim])

    assert agent.run_backrun(ctx) is None


def test_backrun_strategy_picks_largest_victim() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    small = SwapAction(agent_id="victim-a", token_in="USDC", token_out="SOL", amount_in=20_000)
    large = SwapAction(agent_id="victim-b", token_in="USDT", token_out="BONK", amount_in=500_000)
    ctx = DecisionContext(pending_actions=[small, large])

    bundle = agent.run_backrun(ctx)
    assert bundle is not None
    backrun_swap = bundle.txs[0].actions[0]
    assert isinstance(backrun_swap, SwapAction)
    # Largest victim wins: BONK->USDT pair selected for the back-run.
    assert backrun_swap.token_in == "BONK"
    assert backrun_swap.token_out == "USDT"


def test_sandwich_strategy_emits_three_tx_bundle() -> None:
    """PRD US-013 line 1062: front-run + victim + back-run in one bundle."""
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["sandwich"]))
    victim = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=100_000)
    ctx = DecisionContext(pending_actions=[victim])

    bundle = agent.run_sandwich(ctx)

    assert bundle is not None
    assert len(bundle.txs) == 3
    # Position 0: front-run, same direction as the victim.
    front_run = bundle.txs[0].actions[0]
    assert isinstance(front_run, SwapAction)
    assert front_run.token_in == "USDC"
    assert front_run.token_out == "SOL"
    assert front_run.agent_id == "searcher-1"
    # Position 1: the victim swap, copied verbatim.
    assert bundle.txs[1].actions[0] is victim
    # Position 2: back-run, opposite direction.
    back_run = bundle.txs[2].actions[0]
    assert isinstance(back_run, SwapAction)
    assert back_run.token_in == "SOL"
    assert back_run.token_out == "USDC"
    assert back_run.agent_id == "searcher-1"
    # Tip clears the protocol minimum and rides on the back-run tx.
    assert bundle.tip_lamports >= MIN_BUNDLE_TIP_LAMPORTS
    assert bundle.tip_payments[0].tx_index == 2
    assert bundle.tip_payments[0].location == "instruction"
    assert bundle.tip_payments[0].recipient == "tip-account"


def test_sandwich_strategy_returns_none_without_pending_actions() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["sandwich"]))
    assert agent.run_sandwich(DecisionContext()) is None


def test_sandwich_strategy_returns_none_when_strategy_not_configured() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    victim = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=100_000)
    ctx = DecisionContext(pending_actions=[victim])

    assert agent.run_sandwich(ctx) is None


def test_sandwich_strategy_skips_victim_below_min_ev_threshold() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["sandwich"]))
    small = SwapAction(agent_id="victim", token_in="USDC", token_out="SOL", amount_in=1_000)
    ctx = DecisionContext(pending_actions=[small])

    assert agent.run_sandwich(ctx) is None


# PRD US-013 line 1049: per-strategy, per-run tracking metrics.


def test_metrics_record_submitted_increments_bundles_and_tips_submitted() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("backrun", tip_lamports=5_000)
    metrics.record_submitted("backrun", tip_lamports=3_000)

    bucket = metrics.by_strategy["backrun"]
    assert bucket.bundles_submitted == 2
    assert bucket.tips_submitted_lamports == 8_000
    # Tips are not paid until the bundle lands.
    assert bucket.bundles_landed == 0
    assert bucket.tips_paid_lamports == 0


def test_metrics_record_landed_credits_paid_tip_and_realized_ev() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("backrun", tip_lamports=5_000)
    metrics.record_landed("backrun", tip_lamports=5_000, realized_ev_lamports=100_000)

    bucket = metrics.by_strategy["backrun"]
    assert bucket.bundles_landed == 1
    assert bucket.tips_paid_lamports == 5_000
    assert bucket.realized_ev_lamports == 100_000


def test_metrics_landing_rate_is_landed_over_submitted() -> None:
    metrics = JitoSearcherMetrics()
    for _ in range(4):
        metrics.record_submitted("backrun", tip_lamports=1_000)
    metrics.record_landed("backrun", tip_lamports=1_000, realized_ev_lamports=10_000)

    assert metrics.landing_rate("backrun") == pytest.approx(0.25)


def test_metrics_tip_roi_is_realized_ev_over_tip_paid() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("sandwich", tip_lamports=2_000)
    metrics.record_landed("sandwich", tip_lamports=2_000, realized_ev_lamports=50_000)

    # ROI = 50_000 / 2_000 = 25.0
    assert metrics.tip_roi("sandwich") == pytest.approx(25.0)


def test_metrics_landing_rate_returns_zero_for_strategy_with_no_submissions() -> None:
    metrics = JitoSearcherMetrics()
    assert metrics.landing_rate("backrun") == 0.0


def test_metrics_tip_roi_returns_zero_when_no_tip_paid() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("backrun", tip_lamports=5_000)
    # No landings yet -> tips_paid_lamports == 0.
    assert metrics.tip_roi("backrun") == 0.0


def test_metrics_are_isolated_per_strategy() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("backrun", tip_lamports=1_000)
    metrics.record_submitted("sandwich", tip_lamports=2_000)
    metrics.record_landed("backrun", tip_lamports=1_000, realized_ev_lamports=10_000)

    assert metrics.by_strategy["backrun"].bundles_landed == 1
    assert metrics.by_strategy["sandwich"].bundles_landed == 0
    assert metrics.tip_roi("sandwich") == 0.0
    assert metrics.tip_roi("backrun") == pytest.approx(10.0)


def test_searcher_initializes_empty_metrics_object() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    assert isinstance(agent.metrics, JitoSearcherMetrics)
    assert agent.metrics.by_strategy == {}


# PRD US-013 line 1053: surface metrics in run snapshot with synthetic flag.


def test_metrics_to_snapshot_dict_carries_synthetic_marker() -> None:
    metrics = JitoSearcherMetrics()
    metrics.record_submitted("backrun", tip_lamports=5_000)
    metrics.record_landed("backrun", tip_lamports=5_000, realized_ev_lamports=100_000)

    snapshot = metrics.to_snapshot_dict()

    assert snapshot["synthetic"] is True
    by_strategy = snapshot["by_strategy"]
    assert isinstance(by_strategy, dict)
    backrun = by_strategy["backrun"]
    assert backrun["bundles_submitted"] == 1
    assert backrun["bundles_landed"] == 1
    assert backrun["tips_submitted_lamports"] == 5_000
    assert backrun["tips_paid_lamports"] == 5_000
    assert backrun["realized_ev_lamports"] == 100_000
    assert backrun["landing_rate"] == pytest.approx(1.0)
    assert backrun["tip_roi"] == pytest.approx(20.0)


def test_metrics_to_snapshot_dict_empty_when_no_strategies_used() -> None:
    metrics = JitoSearcherMetrics()
    snapshot = metrics.to_snapshot_dict()
    assert snapshot == {"synthetic": True, "by_strategy": {}}


# PRD US-013 line 1063: tip sizing for the linear curve matches 5% of EV
# at the default slope (0.05).


def test_tip_sizing_linear_curve_matches_5pct_of_ev() -> None:
    curve = TipCurveSpec(kind="linear")
    assert curve.apply(expected_ev=100_000, fee_quote=0) == 5_000


# PRD US-013 line 1064: tip sizing for the percent_of_ev curve returns
# percent * EV at the default percent (0.5 → 50% of EV).


def test_tip_sizing_percent_of_ev_curve() -> None:
    curve = TipCurveSpec(kind="percent_of_ev")
    assert curve.apply(expected_ev=100_000, fee_quote=0) == 50_000


# PRD US-013 line 1065: size_tip queries the priority-fee market at the
# configured ``priority_fee_percentile_target``.


class _RecordingFeeMarket:
    """Stub ``PriorityFeeMarket`` that records ``quote`` call args."""

    def __init__(self, return_value: int = 0) -> None:
        self.return_value = return_value
        self.calls: list[tuple[str, int]] = []

    def quote(self, account_id: str, percentile: int) -> int:
        self.calls.append((account_id, percentile))
        return self.return_value


def test_tip_sizing_queries_fee_market_at_target_percentile() -> None:
    params = JitoSearcherParams(
        strategies=["backrun"],
        tip_curve=TipCurveSpec(kind="linear"),
        min_ev_to_submit_lamports=10_000,
        tip_account="tip-account",
        priority_fee_percentile_target=90,
    )
    agent = JitoSearcher(agent_id="searcher-1", params=params)
    fee_market = _RecordingFeeMarket(return_value=1_234)
    ctx = DecisionContext(priority_fee_market=fee_market)

    agent.size_tip(expected_ev=100_000, ctx=ctx, target_account="pool-acct")

    assert fee_market.calls == [("pool-acct", 90)]


# PRD US-013 line 1066: opportunities below ``min_ev_to_submit_lamports``
# are skipped — neither the backrun nor the sandwich strategy emits a bundle.


def test_min_ev_threshold_skips_low_value_opportunities() -> None:
    backrun_agent = JitoSearcher(
        agent_id="searcher-backrun", params=_params(["backrun"])
    )
    sandwich_agent = JitoSearcher(
        agent_id="searcher-sandwich", params=_params(["sandwich"])
    )
    # ``amount_in`` (EV proxy) sits below the 10_000 threshold from ``_params``.
    low_value = SwapAction(
        agent_id="victim", token_in="USDC", token_out="SOL", amount_in=1_000
    )
    ctx = DecisionContext(pending_actions=[low_value])

    assert backrun_agent.run_backrun(ctx) is None
    assert sandwich_agent.run_sandwich(ctx) is None


# PRD US-013 line 1067: searcher metrics block carries the ``synthetic``
# marker until 2.1 calibrates the priors.


def test_metrics_carry_synthetic_marker() -> None:
    agent = JitoSearcher(agent_id="searcher-1", params=_params(["backrun"]))
    agent.metrics.record_submitted("backrun", tip_lamports=5_000)

    snapshot = agent.metrics.to_snapshot_dict()

    assert snapshot["synthetic"] is True
