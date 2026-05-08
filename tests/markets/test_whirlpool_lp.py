"""Unit tests for the Whirlpool concentrated-liquidity LP path.

Builds a tiny synthetic Whirlpool pool inline (no corpus dependency) so
deposit / withdraw / fee-collection math can be exercised in isolation.
"""

from __future__ import annotations

import pytest

from defi_sim.core.market import ConcentratedLPPosition
from defi_sim.core.types import (
    AgentState,
    ExecutionContext,
    LPAction,
    LPActionType,
    SwapAction,
    Token,
)
from defi_sim.markets.whirlpool import (
    TickArrayState,
    TickEntry,
    WhirlpoolMarket,
    WhirlpoolPoolState,
)
from defi_sim.markets.whirlpool_math import (
    sqrt_price_from_tick_index,
)


TICK_SPACING = 64
TICK_ARRAY_SIZE = 88
ARRAY_SPAN = TICK_ARRAY_SIZE * TICK_SPACING  # 5632


def _make_market(
    *,
    initial_liquidity: int = 1_000_000_000,
    tick_current: int = 0,
    fee_rate: int = 3000,
    vault_a: int = 1_000_000_000,
    vault_b: int = 1_000_000_000,
) -> WhirlpoolMarket:
    """Tiny SOL/USDC-shaped pool centered at tick 0 (price ≈ 1, no decimal scaling)."""
    pool = WhirlpoolPoolState(
        pubkey="test_pool",
        tick_spacing=TICK_SPACING,
        fee_rate=fee_rate,
        protocol_fee_rate=0,
        liquidity=initial_liquidity,
        sqrt_price_x64=sqrt_price_from_tick_index(tick_current),
        tick_current_index=tick_current,
        token_mint_a="MINT_A",
        token_mint_b="MINT_B",
        token_vault_a_pubkey="VAULT_A",
        token_vault_b_pubkey="VAULT_B",
        token_vault_a_amount=vault_a,
        token_vault_b_amount=vault_b,
        token_decimals_a=6,  # equal decimals → no scaling factor
        token_decimals_b=6,
    )
    arrays = [
        TickArrayState(
            pubkey=f"array_{start}",
            start_tick_index=start,
            ticks=[TickEntry() for _ in range(TICK_ARRAY_SIZE)],
        )
        for start in (-ARRAY_SPAN, 0, ARRAY_SPAN)
    ]
    return WhirlpoolMarket(
        pool=pool,
        tick_arrays=arrays,
        token_a=Token(id="A", symbol="A", decimals=6),
        token_b=Token(id="B", symbol="B", decimals=6),
    )


def _ctx(market: WhirlpoolMarket, balances: dict[str, int]) -> ExecutionContext:
    state = AgentState(agent_id="lp1", balances=dict(balances))
    return ExecutionContext(agent_state=state)


def test_deposit_in_range_consumes_both_tokens_and_bumps_active_l() -> None:
    market = _make_market()
    initial_l = market.pool.liquidity
    initial_va = market.pool.token_vault_a_amount
    initial_vb = market.pool.token_vault_b_amount

    # ±~5% range around spot=1 → roughly ticks [-512, 512] (snapped to spacing).
    action = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=10_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.95, 1.05),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})

    result = market.execute(action, ctx)
    assert result.success, result.error

    pos = market.get_lp_position("lp1")
    assert isinstance(pos, ConcentratedLPPosition)
    assert pos.in_range is True
    assert pos.liquidity > 0
    assert pos.tick_lower < 0 < pos.tick_upper

    # Both tokens flowed in.
    assert result.token_deltas["A"] < 0
    assert result.token_deltas["B"] < 0

    # Active liquidity rose; vaults gained the deposit.
    assert market.pool.liquidity == initial_l + pos.liquidity
    assert market.pool.token_vault_a_amount == initial_va + (-result.token_deltas["A"])
    assert market.pool.token_vault_b_amount == initial_vb + (-result.token_deltas["B"])


def test_deposit_above_current_is_token_a_only() -> None:
    """Range above the spot mints with token A only.

    CLMM convention: when current < lower, the LP's position is all
    token A and converts to token B as price rises through the range.
    """
    market = _make_market(tick_current=0)
    action = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(1.10, 1.20),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})

    result = market.execute(action, ctx)
    assert result.success, result.error
    assert result.token_deltas["A"] < 0
    assert result.token_deltas.get("B", 0) == 0

    pos = market.get_lp_position("lp1")
    assert pos.in_range is False


def test_deposit_below_current_is_token_b_only() -> None:
    """Range below the spot mints with token B only."""
    market = _make_market(tick_current=0)
    action = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.80, 0.90),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})

    result = market.execute(action, ctx)
    assert result.success, result.error
    assert result.token_deltas.get("A", 0) == 0
    assert result.token_deltas["B"] < 0

    pos = market.get_lp_position("lp1")
    assert pos.in_range is False


def test_withdraw_round_trip_returns_principal_within_rounding() -> None:
    market = _make_market()
    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=10_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.95, 1.05),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})
    deposit_result = market.execute(deposit, ctx)
    assert deposit_result.success

    deposited_a = -deposit_result.token_deltas["A"]
    deposited_b = -deposit_result.token_deltas["B"]
    initial_l_after_deposit = market.pool.liquidity

    # No swaps — withdraw should return the same amounts (mod rounding).
    withdraw = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=0,  # ignored by Whirlpool
        lp_type=LPActionType.WITHDRAW,
    )
    result = market.execute(withdraw, ctx)
    assert result.success, result.error

    # Exact round-trip is allowed to lose at most 1 raw unit per side
    # to round-up-on-deposit / round-down-on-withdraw asymmetry.
    assert abs(int(result.token_deltas["A"]) - deposited_a) <= 1
    assert abs(int(result.token_deltas["B"]) - deposited_b) <= 1

    # Active liquidity returned to its pre-deposit value.
    pos_l = initial_l_after_deposit - market.pool.liquidity
    assert pos_l > 0
    # And the position is gone.
    assert market.get_lp_position("lp1") is None


def test_withdraw_collects_fees_from_intervening_swap() -> None:
    market = _make_market(initial_liquidity=0, vault_a=10_000_000, vault_b=10_000_000)
    # With initial_liquidity=0, the only L the swap will see comes from
    # our LP — clean fee attribution.
    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.90, 1.10),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})
    dep_result = market.execute(deposit, ctx)
    assert dep_result.success, dep_result.error
    assert market.pool.liquidity > 0

    # A small a-to-b swap at our liquidity charges a fee that should
    # accrue entirely to our LP (we own all the active L).
    trader = AgentState(agent_id="trader", balances={"A": 10_000_000, "B": 0})
    swap_ctx = ExecutionContext(agent_state=trader)
    swap = SwapAction(
        agent_id="trader", token_in="A", token_out="B", amount_in=100_000
    )
    swap_result = market.execute(swap, swap_ctx)
    assert swap_result.success, swap_result.error
    fee_paid = int(swap_result.fee_splits["lp"])
    assert fee_paid > 0

    pos_record = market.position_record("lp1")
    assert pos_record is not None
    market._collect_fees_into_position(pos_record)
    # The swap was a-side, so fees accrue to side A. Allow ±1 for the
    # u128 fee-growth fixed-point rounding.
    assert pos_record.accumulated_fees_a > 0
    assert abs(int(pos_record.accumulated_fees_a) - fee_paid) <= 2


def test_position_round_stats_track_in_range_fraction() -> None:
    market = _make_market()
    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.95, 1.05),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})
    market.execute(deposit, ctx)

    # 5 in-range ticks, then move price out and tick 5 more out-of-range.
    for _ in range(5):
        market.tick_lp_round_stats()
    market.pool.tick_current_index = 10_000  # well above the upper tick
    for _ in range(5):
        market.tick_lp_round_stats()

    pos = market.position_record("lp1")
    assert pos.total_rounds == 10
    assert pos.in_range_rounds == 5


def test_telemetry_total_lp_liquidity_is_stable_under_price_drift() -> None:
    """``total_lp_liquidity`` is the sum of L across all minted positions
    and only changes on mint / burn. Active L (``active_liquidity``)
    drops when price drifts past the position's range; total LP L does
    not. This is what the "Total LP Deposits Over Time" chart reads."""
    market = _make_market()
    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.95, 1.05),
    )
    ctx = _ctx(market, {"A": 100_000_000, "B": 100_000_000})
    result = market.execute(deposit, ctx)
    assert result.success

    pos = market.position_record("lp1")
    assert pos is not None and pos.liquidity > 0

    telemetry_in_range = market.pop_round_telemetry()
    assert telemetry_in_range["active_liquidity"] > 0
    # total_lp_liquidity sums positions that the *agent* minted —
    # ambient pool liquidity (the constructor's ``initial_liquidity``)
    # is in pool.liquidity but isn't tracked as a position.
    assert telemetry_in_range["total_lp_liquidity"] == pos.liquidity
    # Synthetic ``_make_market`` has no chain-hydrated positions, so
    # the construction-time baseline is zero and agent_lp_liquidity
    # equals the mint.
    assert telemetry_in_range["baseline_lp_liquidity"] == 0
    assert telemetry_in_range["agent_lp_liquidity"] == pos.liquidity

    # Drift price out of range without any LP activity. Active L drops
    # to the ambient floor; total LP L is unchanged because no
    # mint/burn happened.
    market.pool.tick_current_index = 10_000
    market.pool.liquidity = 1_000_000_000  # ambient, position's L removed by tick crossing
    telemetry_out_of_range = market.pop_round_telemetry()
    assert telemetry_out_of_range["active_liquidity"] == 1_000_000_000
    assert telemetry_out_of_range["total_lp_liquidity"] == pos.liquidity
    assert telemetry_out_of_range["agent_lp_liquidity"] == pos.liquidity

    # Withdraw burns the position; total and agent L drop to zero.
    withdraw = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=0,
        lp_type=LPActionType.WITHDRAW,
    )
    market.execute(withdraw, ctx)
    telemetry_after_burn = market.pop_round_telemetry()
    assert telemetry_after_burn["total_lp_liquidity"] == 0
    assert telemetry_after_burn["agent_lp_liquidity"] == 0


def test_telemetry_baseline_captures_chain_hydrated_positions() -> None:
    """When the market is constructed with tick arrays that already
    carry liquidity_gross (the fork-hydrated case), the baseline picks
    them up so subsequent mints register as ``agent_lp_liquidity`` on
    top, not as part of the baseline."""
    market = _make_market()
    # Simulate hydration: seed two ticks with gross L matching what a
    # real on-chain position of L=2_000 would imprint (each tick gets
    # +L on its boundary).
    seed_array = next(ta for ta in market._tick_arrays if ta.start_tick_index == 0)
    seed_array.ticks[0].initialized = True
    seed_array.ticks[0].liquidity_gross = 2_000
    seed_array.ticks[5].initialized = True
    seed_array.ticks[5].liquidity_gross = 2_000

    # Re-bind a fresh market over the seeded arrays so __init__'s
    # baseline snapshot picks them up. (In production, fork builders
    # construct WhirlpoolMarket *after* hydration.)
    from defi_sim.core.types import Token

    seeded = WhirlpoolMarket(
        pool=market.pool,
        tick_arrays=market._tick_arrays,
        token_a=Token(id="A", symbol="A", decimals=6),
        token_b=Token(id="B", symbol="B", decimals=6),
    )
    telemetry = seeded.pop_round_telemetry()
    assert telemetry["baseline_lp_liquidity"] == 2_000
    assert telemetry["total_lp_liquidity"] == 2_000
    assert telemetry["agent_lp_liquidity"] == 0

    deposit = LPAction(
        agent_id="lp1",
        collateral="B",
        amount=5_000_000,
        lp_type=LPActionType.DEPOSIT,
        price_range=(0.95, 1.05),
    )
    seeded.execute(deposit, _ctx(seeded, {"A": 100_000_000, "B": 100_000_000}))
    pos = seeded.position_record("lp1")
    assert pos is not None and pos.liquidity > 0

    after = seeded.pop_round_telemetry()
    assert after["baseline_lp_liquidity"] == 2_000  # unchanged
    assert after["total_lp_liquidity"] == 2_000 + pos.liquidity
    assert after["agent_lp_liquidity"] == pos.liquidity
