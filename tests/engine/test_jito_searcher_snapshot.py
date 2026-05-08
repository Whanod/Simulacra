"""Tests for ``JitoSearcher`` metrics on ``RoundSnapshot.metrics``.

PRD US-013 line 1058 (Validation): the run snapshot carries the
``synthetic`` marker on the searcher's metrics block.
"""

from __future__ import annotations

from defi_sim.agents.jito_searcher import JitoSearcher, JitoSearcherParams
from defi_sim.agents.tip_curve import TipCurveSpec
from defi_sim.engine.api import build_engine


def _minimal_spec() -> dict:
    return {
        "market": {
            "type": "cfamm",
            "tokens": [
                {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
            ],
            "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
        },
        "agents": [],
        "num_rounds": 1,
        "snapshot_interval": 1,
        "seed": 1,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
            "params": {"cost_token": "USDC"},
        },
    }


def _searcher(agent_id: str = "searcher-1") -> JitoSearcher:
    params = JitoSearcherParams(
        strategies=["backrun"],
        tip_curve=TipCurveSpec(kind="linear"),
        min_ev_to_submit_lamports=10_000,
        tip_account="tip-account",
    )
    return JitoSearcher(agent_id=agent_id, params=params)


def test_run_snapshot_metrics_carry_synthetic_marker_for_searcher() -> None:
    """PRD US-013 line 1058: searcher metrics block on the run snapshot
    carries ``synthetic: True`` until 2.1 calibrates the priors.
    """
    engine = build_engine(_minimal_spec())
    searcher = _searcher(agent_id="searcher-1")
    searcher.metrics.record_submitted("backrun", tip_lamports=5_000)
    searcher.metrics.record_landed("backrun", tip_lamports=5_000, realized_ev_lamports=100_000)
    engine._agents.append(searcher)

    metrics = engine._collect_snapshot_metrics()

    assert "jito_searcher" in metrics
    block = metrics["jito_searcher"]["searcher-1"]
    assert block["synthetic"] is True


def test_run_snapshot_metrics_omit_jito_searcher_when_no_searcher_registered() -> None:
    """No ``JitoSearcher`` agent → no ``jito_searcher`` key on metrics
    (keeps non-Solana snapshots clean — matches the ``validator_revenue``
    precedent at simulation.py:1535).
    """
    engine = build_engine(_minimal_spec())

    metrics = engine._collect_snapshot_metrics()

    assert "jito_searcher" not in metrics


def test_run_snapshot_metrics_idle_searcher_still_carries_synthetic_marker() -> None:
    """An idle searcher (no submissions) still gets a snapshot bucket with
    ``synthetic: True`` — snapshot-shape stability across runs.
    """
    engine = build_engine(_minimal_spec())
    engine._agents.append(_searcher(agent_id="searcher-idle"))

    metrics = engine._collect_snapshot_metrics()

    block = metrics["jito_searcher"]["searcher-idle"]
    assert block["synthetic"] is True
    assert block["by_strategy"] == {}
