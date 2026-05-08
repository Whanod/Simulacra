"""Range-aware LP streaming metrics — closed-form sanity checks.

The three metrics ride on the Whirlpool ConcentratedLP surface:
  * ``LPInRangeFraction`` — fraction of rounds the spot tick fell inside
    the position's [tick_lower, tick_upper) band.
  * ``RangeIL`` — bounded impermanent loss vs. the LP's HODL portfolio.
  * ``FeesVsILBreakeven`` — accumulated fees / |IL|, both in quote units.

Each test drives the metric with a synthetic price path or position so
the expected output is computable by hand.
"""

from __future__ import annotations

from defi_sim.core.types import (
    AgentState,
    ExecutionContext,
    LPAction,
    LPActionType,
    Token,
)
from defi_sim.markets.whirlpool import (
    TickArrayState,
    TickEntry,
    WhirlpoolMarket,
    WhirlpoolPoolState,
)
from defi_sim.markets.whirlpool_math import sqrt_price_from_tick_index
from defi_sim.metrics.generic import (
    FeesVsILBreakeven,
    LPInRangeFraction,
    RangeIL,
)


TICK_SPACING = 64
TICK_ARRAY_SIZE = 88
ARRAY_SPAN = TICK_ARRAY_SIZE * TICK_SPACING


def _make_market(tick_current: int = 0) -> WhirlpoolMarket:
    pool = WhirlpoolPoolState(
        pubkey="m",
        tick_spacing=TICK_SPACING,
        fee_rate=3000,
        protocol_fee_rate=0,
        liquidity=0,
        sqrt_price_x64=sqrt_price_from_tick_index(tick_current),
        tick_current_index=tick_current,
        token_vault_a_amount=10_000_000,
        token_vault_b_amount=10_000_000,
        token_decimals_a=6,
        token_decimals_b=6,
    )
    arrays = [
        TickArrayState(
            pubkey=f"a_{s}",
            start_tick_index=s,
            ticks=[TickEntry() for _ in range(TICK_ARRAY_SIZE)],
        )
        for s in (-ARRAY_SPAN, 0, ARRAY_SPAN)
    ]
    return WhirlpoolMarket(
        pool=pool,
        tick_arrays=arrays,
        token_a=Token(id="A", symbol="A", decimals=6),
        token_b=Token(id="B", symbol="B", decimals=6),
    )


def _seed_position(market: WhirlpoolMarket, price_range=(0.95, 1.05)) -> None:
    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=2_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=price_range,
    )
    ctx = ExecutionContext(
        agent_state=AgentState(agent_id="lp1", balances={"A": 100_000_000, "B": 100_000_000})
    )
    result = market.execute(deposit, ctx)
    assert result.success, result.error


def test_lp_in_range_fraction_reflects_tick_path() -> None:
    """6 rounds in-range, 4 rounds out-of-range → fraction == 0.6."""
    market = _make_market(tick_current=0)
    _seed_position(market, price_range=(0.95, 1.05))

    metric = LPInRangeFraction(market=market)

    for _ in range(6):
        metric.on_round(0, 0, None)
    market.pool.tick_current_index = 10_000  # well above upper bound
    for _ in range(4):
        metric.on_round(0, 0, None)

    assert metric.finalize() == 0.6


def test_range_il_positive_after_price_move() -> None:
    """Price drift inside the range produces non-zero IL (LP value < HODL)."""
    market = _make_market(tick_current=0)
    _seed_position(market, price_range=(0.90, 1.10))

    metric = RangeIL(market=market)

    metric.on_round(0, 0, None)  # Initial sample: IL ≈ 0
    initial = metric.finalize()
    assert initial == 0.0 or abs(initial) < 1e-3

    # Move spot up — IL should accumulate.
    market.pool.tick_current_index = 200
    market.pool.sqrt_price_x64 = sqrt_price_from_tick_index(200)
    metric.on_round(0, 0, None)

    final = metric.finalize()
    assert final > 0.0  # bounded IL is non-negative


def test_fees_vs_il_breakeven_zero_when_no_fees() -> None:
    """No swaps → no fees → break-even ratio is 0 (loss-only)."""
    market = _make_market(tick_current=0)
    _seed_position(market, price_range=(0.90, 1.10))

    metric = FeesVsILBreakeven(market=market)

    market.pool.tick_current_index = 200
    market.pool.sqrt_price_x64 = sqrt_price_from_tick_index(200)
    metric.on_round(0, 0, None)

    assert metric.finalize() == 0.0


def test_fees_vs_il_breakeven_winning_when_fees_dominate() -> None:
    """Fees ≫ IL → break-even ratio ≫ 1 (LP is winning)."""
    market = _make_market(tick_current=0)
    _seed_position(market, price_range=(0.90, 1.10))
    # Inject a synthetic fee balance directly on the position so the
    # streaming metric sees fees without us having to drive a swap.
    # IL at unchanged spot is ~rounding-error (a few raw units), so any
    # meaningful fee dominates it by orders of magnitude.
    pos = market.position_record("lp1")
    pos.accumulated_fees_b = 1_000_000

    metric = FeesVsILBreakeven(market=market)
    metric.on_round(0, 0, None)
    assert metric.finalize() > 1000.0


def test_lp_fees_per_liquidity_scales_with_fees() -> None:
    """Engine-level ``lp_fees_per_liquidity`` derived metric tracks fee revenue.

    Halving the injected fee balance must halve the per-position metric
    (TVL is unchanged across the two arms), and the value should be a
    sensibly-scaled dimensionless ratio (not the ~1e-13 raw-L denominator
    that previously rounded to zero in the UI).
    """
    from defi_sim.engine.config import SimulationConfig
    from defi_sim.engine.simulation import SimulationEngine

    def _arm(fee_b: int) -> dict[str, float | None]:
        market = _make_market(tick_current=0)
        _seed_position(market, price_range=(0.90, 1.10))
        pos = market.position_record("lp1")
        pos.accumulated_fees_b = fee_b
        engine = SimulationEngine(
            market=market,
            agents=[],
            config=SimulationConfig(num_rounds=0),
        )
        out: dict[str, float | None] = {}
        engine._populate_clmm_lp_metrics(out)
        return out

    high = _arm(1_000_000)
    low = _arm(500_000)

    # Per-position keys present and halve cleanly.
    assert "lp_fees_per_liquidity:lp1" in high
    assert "lp_fees_per_liquidity:lp1" in low
    assert high["lp_fees_per_liquidity:lp1"] > 0
    # Dimensionless yield: must be in a sane band, not ~0 (raw-L bug)
    # and not >1 (would mean fees exceeded position notional).
    assert 1e-4 < high["lp_fees_per_liquidity:lp1"] < 1.0
    ratio = low["lp_fees_per_liquidity:lp1"] / high["lp_fees_per_liquidity:lp1"]
    assert 0.49 < ratio < 0.51

    # Aggregate matches the single-position case.
    assert high["lp_fees_per_liquidity"] == high["lp_fees_per_liquidity:lp1"]


def test_lp_fees_per_liquidity_absent_when_no_position() -> None:
    """Pool with no LP positions emits no ``lp_fees_per_liquidity`` key."""
    from defi_sim.engine.config import SimulationConfig
    from defi_sim.engine.simulation import SimulationEngine

    market = _make_market(tick_current=0)
    engine = SimulationEngine(
        market=market,
        agents=[],
        config=SimulationConfig(num_rounds=0),
    )
    out: dict[str, float | None] = {}
    engine._populate_clmm_lp_metrics(out)
    assert "lp_fees_per_liquidity" not in out
