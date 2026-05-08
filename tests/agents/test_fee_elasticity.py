"""Fee-elasticity behavior of NoiseTrader and SwapNoiseTrader.

Demo scaffolding: the lighthouse template's "lower fees attract more flow"
counterfactual depends on noise / swap-noise agents reading the live
``AmmSnapshot.fee_bps`` and rescaling their trade sizes accordingly. These
tests pin both the back-compat contract (``fee_elasticity == 0`` reproduces
the prior behavior bit-for-bit) and the directional contract (lower fees
produce strictly larger expected trade sizes).
"""

from __future__ import annotations

import numpy as np

from defi_sim.agents.noise import NoiseParams, NoiseTrader
from defi_sim.agents.swap_noise import SwapNoiseParams, SwapNoiseTrader
from defi_sim.core.agent import DecisionContext
from defi_sim.core.types import AgentState, AmmSnapshot, BundleAction, SingleAssetAction, SwapAction


def _make_amm_snapshot(fee_bps: float | None) -> AmmSnapshot:
    return AmmSnapshot(
        num_assets=2,
        tokens=["USDC", "SOL"],
        reserves={"USDC": 10**12, "SOL": 10**12},
        prices={"USDC": 1, "SOL": 100},
        total_liquidity=10**12,
        invariant=10**12,
        fee_bps=fee_bps,
    )


def _noise_decisions(
    elasticity: float,
    fee_bps: float | None,
    *,
    seed: int = 1337,
    rounds: int = 200,
) -> list[int]:
    """Run NoiseTrader for ``rounds`` decisions, returning emitted trade sizes.

    Bundles are disabled so every emission is a SingleAssetAction with a
    deterministic ``amount`` field — keeps the assertion math simple.
    """
    agent = NoiseTrader(
        agent_id="noise-test",
        params=NoiseParams(
            collateral="USDC",
            trade_min=100_000,
            trade_max=5_000_000,
            frequency=1.0,  # always fire so size scaling is what we measure
            bundle_probability=0.0,
            bidirectional=False,
            fee_elasticity=elasticity,
            reference_fee_bps=30.0,
        ),
        rng=np.random.default_rng(seed),
    )
    snapshot = _make_amm_snapshot(fee_bps)
    sizes: list[int] = []
    for r in range(rounds):
        ctx = DecisionContext(
            market_state=snapshot,
            current_round=r,
            total_rounds=rounds,
            agent_state=AgentState(
                agent_id="noise-test",
                balances={"USDC": 10**18, "SOL": 10**18},
            ),
        )
        actions = agent.decide(ctx)
        for action in actions:
            assert isinstance(action, (SingleAssetAction, BundleAction))
            sizes.append(int(action.amount))
    return sizes


def _swap_noise_decisions(
    elasticity: float,
    fee_bps: float | None,
    *,
    seed: int = 1337,
    rounds: int = 200,
) -> list[int]:
    agent = SwapNoiseTrader(
        agent_id="swap-test",
        params=SwapNoiseParams(
            token_in="USDC",
            token_out="SOL",
            amount_min=500_000,
            amount_max=25_000_000,
            frequency=1.0,
            cu_price_min=1_000,
            cu_price_max=80_000,
            fee_elasticity=elasticity,
            reference_fee_bps=30.0,
        ),
        rng=np.random.default_rng(seed),
    )
    snapshot = _make_amm_snapshot(fee_bps)
    sizes: list[int] = []
    for r in range(rounds):
        ctx = DecisionContext(
            market_state=snapshot,
            current_round=r,
            total_rounds=rounds,
            agent_state=AgentState(
                agent_id="swap-test",
                balances={"USDC": 10**18, "SOL": 10**18},
            ),
        )
        actions = agent.decide(ctx)
        for action in actions:
            assert isinstance(action, SwapAction)
            sizes.append(int(action.amount_in))
    return sizes


def test_noise_elasticity_zero_is_backcompat() -> None:
    """``fee_elasticity == 0`` must be a no-op regardless of ``fee_bps``."""
    sizes_no_fee = _noise_decisions(elasticity=0.0, fee_bps=None)
    sizes_30 = _noise_decisions(elasticity=0.0, fee_bps=30.0)
    sizes_15 = _noise_decisions(elasticity=0.0, fee_bps=15.0)
    assert sizes_no_fee == sizes_30 == sizes_15
    assert len(sizes_no_fee) > 0


def test_noise_elasticity_one_doubles_at_half_fee() -> None:
    """elasticity=1 with fee_bps=15 vs ref 30 → mean trade size ~2× baseline."""
    sizes_30 = _noise_decisions(elasticity=1.0, fee_bps=30.0)
    sizes_15 = _noise_decisions(elasticity=1.0, fee_bps=15.0)
    assert sizes_30 and sizes_15
    mean_30 = float(np.mean(sizes_30))
    mean_15 = float(np.mean(sizes_15))
    # Theoretical ratio is 2.0; allow ±20% slack for the integer-clamped
    # uniform draw on a 200-decision sample.
    ratio = mean_15 / mean_30
    assert 1.6 < ratio < 2.4, f"expected ~2x volume uplift, got {ratio:.2f}"


def test_noise_missing_fee_bps_is_no_op() -> None:
    """Snapshots without ``fee_bps`` (older AMMs) must not break elasticity > 0."""
    sizes_off = _noise_decisions(elasticity=0.0, fee_bps=None)
    sizes_on = _noise_decisions(elasticity=1.0, fee_bps=None)
    assert sizes_off == sizes_on


def test_swap_noise_elasticity_zero_is_backcompat() -> None:
    sizes_no_fee = _swap_noise_decisions(elasticity=0.0, fee_bps=None)
    sizes_30 = _swap_noise_decisions(elasticity=0.0, fee_bps=30.0)
    sizes_15 = _swap_noise_decisions(elasticity=0.0, fee_bps=15.0)
    assert sizes_no_fee == sizes_30 == sizes_15
    assert len(sizes_no_fee) > 0


def test_swap_noise_elasticity_one_doubles_at_half_fee() -> None:
    sizes_30 = _swap_noise_decisions(elasticity=1.0, fee_bps=30.0)
    sizes_15 = _swap_noise_decisions(elasticity=1.0, fee_bps=15.0)
    assert sizes_30 and sizes_15
    ratio = float(np.mean(sizes_15)) / float(np.mean(sizes_30))
    assert 1.6 < ratio < 2.4, f"expected ~2x volume uplift, got {ratio:.2f}"


def test_whirlpool_market_snapshot_exposes_fee_bps() -> None:
    """Wiring check: WhirlpoolMarket.get_state().fee_bps reflects pool.fee_rate.

    Whirlpool encodes 1 bp as 100 fee_rate units (denominator 1e6), so
    ``fee_rate=3000`` → 30 bps. Without this wiring the elasticity hook
    above would be inert against real Whirlpool snapshots even though the
    unit tests pass with synthetic ``AmmSnapshot`` instances.
    """
    from defi_sim.core.types import Token
    from defi_sim.markets.whirlpool import (
        TickArrayState,
        TickEntry,
        WhirlpoolMarket,
        WhirlpoolPoolState,
    )
    from defi_sim.markets.whirlpool_math import sqrt_price_from_tick_index

    pool = WhirlpoolPoolState(
        pubkey="t",
        tick_spacing=64,
        fee_rate=3000,  # 30 bps
        protocol_fee_rate=0,
        liquidity=1_000_000_000,
        sqrt_price_x64=sqrt_price_from_tick_index(0),
        tick_current_index=0,
        token_mint_a="A",
        token_mint_b="B",
        token_vault_a_pubkey="va",
        token_vault_b_pubkey="vb",
        token_vault_a_amount=1_000_000_000,
        token_vault_b_amount=1_000_000_000,
        token_decimals_a=6,
        token_decimals_b=6,
    )
    arrays = [
        TickArrayState(
            pubkey=f"a_{s}",
            start_tick_index=s,
            ticks=[TickEntry() for _ in range(88)],
        )
        for s in (-64 * 88, 0, 64 * 88)
    ]
    market = WhirlpoolMarket(
        pool=pool,
        tick_arrays=arrays,
        token_a=Token(id="A", symbol="A", decimals=6),
        token_b=Token(id="B", symbol="B", decimals=6),
    )
    state = market.get_state()
    assert state.fee_bps == 30.0
    pool.fee_rate = 1500
    assert market.get_state().fee_bps == 15.0
