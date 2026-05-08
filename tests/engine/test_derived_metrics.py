"""End-of-run derived metrics in ``SimulationResult.metadata["derived_metrics"]``.

The engine surfaces a small dict of summary statistics (kl_divergence,
convergence_speed, manipulation_cost, slippage, exitability) that the
frontend renders alongside max_drawdown / TWAP / etc. They're best-effort:
when there isn't enough data the value is ``None`` (renders as "—") rather
than a misleading zero.
"""

from __future__ import annotations

import copy
import math


SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "TKN", "symbol": "TKN", "decimals": 6},
            {"id": "USDC", "symbol": "USDC", "decimals": 6},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {
                "collateral": "USDC",
                "frequency": 0.5,
                "trade_min": 100,
                "trade_max": 1_000,
                "bidirectional": True,
            },
            "initial_balances": {"USDC": 10_000_000, "TKN": 10_000_000},
        },
    ],
    "num_rounds": 20,
    "snapshot_interval": 5,
    "seed": 42,
}


def test_derived_metrics_present_in_result_metadata() -> None:
    from defi_sim.engine.api import build_engine

    spec = copy.deepcopy(SPEC)
    engine = build_engine(spec)
    result = engine.run()

    derived = result.metadata.get("derived_metrics")
    assert isinstance(derived, dict), "derived_metrics missing from result.metadata"
    expected_keys = {
        "kl_divergence",
        "convergence_speed",
        "manipulation_cost",
        "slippage",
        "exitability",
    }
    assert expected_keys.issubset(derived.keys()), derived.keys()

    for key, value in derived.items():
        if value is None:
            continue
        assert isinstance(value, float), f"{key} should be float or None, got {type(value)}"
        assert math.isfinite(value), f"{key} must be finite (non-Inf/NaN), got {value}"


def test_slippage_is_finite_when_priced_market_exists() -> None:
    """CFAMM is a PricedMarket — slippage should be a real number, not None."""
    from defi_sim.engine.api import build_engine

    spec = copy.deepcopy(SPEC)
    engine = build_engine(spec)
    result = engine.run()

    derived = result.metadata["derived_metrics"]
    assert derived["slippage"] is not None
    assert 0.0 <= derived["slippage"] <= 1.0


def test_manipulation_cost_none_when_no_tips_paid() -> None:
    """No JitoSearcher / SolanaLikeExecution → no tip outcomes → None."""
    from defi_sim.engine.api import build_engine

    spec = copy.deepcopy(SPEC)
    engine = build_engine(spec)
    result = engine.run()

    derived = result.metadata["derived_metrics"]
    assert derived["manipulation_cost"] is None
