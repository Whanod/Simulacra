"""End-to-end regression test for the LP rebalance-then-redeposit fix.

Drives a tiny in-process Whirlpool sim with a ``PassiveLP`` and a
synthetic "trader" that pushes price up hard enough to repeatedly trip
the LP's IL exit. After each exit the position settles A-heavy, so over
several cycles the LP's collateral (B) balance bleeds toward zero.

Two configurations are compared:

  legacy: ``swap_to_balance_on_redeposit=False`` — every subsequent
          deposit attempt fails with "computed liquidity is zero"
          because one token side has nothing to mint against. The LP
          ends the run stranded with no active position.
  fixed:  ``swap_to_balance_on_redeposit=True``  — once the post-IL
          state appears, the agent emits a SwapAction (alone) to
          rebalance toward 50/50 by value, then deposits successfully
          on the next round. The LP ends the run with a live position.

This pins the bug from the "Liquidity Over Time" chart the user
flagged. If a future change re-strands the LP (e.g., by re-disabling
the swap, breaking the sticky ``_has_held_position`` flag, or removing
the deferred-deposit pattern), the failed-deposit count assertion
catches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from defi_sim.agents.lp import LPParams, PassiveLP
from defi_sim.core.agent import DecisionContext
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
from defi_sim.markets.whirlpool_math import sqrt_price_from_tick_index


TICK_SPACING = 64
TICK_ARRAY_SIZE = 88
ARRAY_SPAN = TICK_ARRAY_SIZE * TICK_SPACING


def _build_market() -> WhirlpoolMarket:
    """Tiny SOL/USDC-shaped pool. Ambient L is sized to dwarf any one
    LP's deposit (mirrors a real Whirlpool fork) so the rebalance
    swap's price impact stays bounded by the helper's
    ``max_pool_fraction`` cap."""
    pool = WhirlpoolPoolState(
        pubkey="e2e_pool",
        tick_spacing=TICK_SPACING,
        fee_rate=3000,
        protocol_fee_rate=0,
        liquidity=5_000_000_000,
        sqrt_price_x64=sqrt_price_from_tick_index(0),
        tick_current_index=0,
        token_mint_a="MINT_A",
        token_mint_b="MINT_B",
        token_vault_a_pubkey="VAULT_A",
        token_vault_b_pubkey="VAULT_B",
        token_vault_a_amount=20_000_000_000,
        token_vault_b_amount=20_000_000_000,
        token_decimals_a=6,
        token_decimals_b=6,
    )
    arrays = [
        TickArrayState(
            pubkey=f"array_{start}",
            start_tick_index=start,
            ticks=[TickEntry() for _ in range(TICK_ARRAY_SIZE)],
        )
        for start in (-ARRAY_SPAN * 2, -ARRAY_SPAN, 0, ARRAY_SPAN, ARRAY_SPAN * 2)
    ]
    return WhirlpoolMarket(
        pool=pool,
        tick_arrays=arrays,
        token_a=Token(id="A", symbol="A", decimals=6),
        token_b=Token(id="B", symbol="B", decimals=6),
    )


@dataclass
class _Outcome:
    failed_deposits: int = 0
    deposits_succeeded: int = 0
    swaps_emitted: int = 0
    has_position_at_end: bool = False
    lp_balances: dict[str, int] = field(default_factory=dict)


def _run(swap_enabled: bool) -> _Outcome:
    market = _build_market()
    params = LPParams(
        collateral="B",
        deposit_fraction=0.5,
        max_loss_threshold=0.05,
        range_mode="symmetric_pct",
        range_width_pct=0.02,
        swap_to_balance_on_redeposit=swap_enabled,
    )
    lp = PassiveLP("lp1", params)
    lp_balances: dict[str, int] = {"A": 1_000_000_000, "B": 1_000_000_000}
    trader_balances: dict[str, int] = {"A": 1_000_000_000_000, "B": 1_000_000_000_000}
    outcome = _Outcome()

    def lp_ctx() -> DecisionContext:
        from defi_sim.core.types import AmmSnapshot

        prices = market.get_prices()
        snap = AmmSnapshot(
            num_assets=len(prices),
            tokens=list(prices.keys()),
            prices=prices,
            reserves={
                "A": market.pool.token_vault_a_amount,
                "B": market.pool.token_vault_b_amount,
            },
        )
        position = market.get_lp_position(lp.agent_id)
        rec = market.position_record(lp.agent_id)
        unrealized_loss = 0.0
        if rec is not None and rec.liquidity > 0:
            v_now = market.position_value_in_b(rec)
            v_hodl = market.hodl_value_in_b(rec)
            if v_hodl > 0:
                unrealized_loss = max(0.0, 1.0 - float(v_now) / float(v_hodl))
        return DecisionContext(
            market_state=snap,
            agent_state=AgentState(agent_id=lp.agent_id, balances=dict(lp_balances)),
            extra={
                "lp_position": position,
                "unrealized_loss": unrealized_loss,
                "token_decimals": {"A": 6, "B": 6},
            },
        )

    def apply(balances: dict[str, int], deltas: dict[str, int]) -> None:
        for tok, delta in deltas.items():
            balances[tok] = balances.get(tok, 0) + int(delta)

    def run_lp() -> None:
        actions = lp.decide(lp_ctx())
        for action in actions:
            ec = ExecutionContext(
                agent_state=AgentState(agent_id=lp.agent_id, balances=dict(lp_balances))
            )
            result = market.execute(action, ec)
            if isinstance(action, SwapAction):
                outcome.swaps_emitted += 1
            if isinstance(action, LPAction) and action.lp_type == LPActionType.DEPOSIT:
                if result.success:
                    outcome.deposits_succeeded += 1
                else:
                    outcome.failed_deposits += 1
            if result.success:
                apply(lp_balances, result.token_deltas)

    def run_trader(amount_in: int) -> None:
        if amount_in <= 0:
            return
        action = SwapAction(
            agent_id="trader",
            token_in="B",
            token_out="A",
            amount_in=amount_in,
        )
        ec = ExecutionContext(
            agent_state=AgentState(agent_id="trader", balances=dict(trader_balances))
        )
        result = market.execute(action, ec)
        if result.success:
            apply(trader_balances, result.token_deltas)

    # Round 0: warm-up — LP deposits, trader does nothing.
    run_lp()
    run_trader(0)

    # Phase 1: 8 rounds of hard buying that drives price up past the
    # LP's ±2% range and trips repeated IL exits.
    for _ in range(8):
        run_lp()
        run_trader(1_500_000_000)

    # Phase 2: 30 quiet rounds. Without intervention, the legacy LP
    # has no path back to a balanced state.
    for _ in range(30):
        run_lp()

    outcome.has_position_at_end = market.get_lp_position(lp.agent_id) is not None
    outcome.lp_balances = dict(lp_balances)
    return outcome


def test_lp_recovers_from_il_exit_when_swap_to_balance_enabled() -> None:
    """The fixed configuration completes the scenario with a live
    position and zero failed deposits."""
    fixed = _run(swap_enabled=True)

    assert fixed.failed_deposits == 0, (
        f"fix should never trip 'computed liquidity is zero', got "
        f"{fixed.failed_deposits} failures"
    )
    assert fixed.swaps_emitted >= 1, (
        "fix should emit at least one rebalance swap during the run"
    )
    assert fixed.has_position_at_end, (
        "fix should leave the LP with a live position after recovery"
    )


def test_lp_strands_after_il_exit_when_swap_disabled() -> None:
    """The legacy configuration reproduces the original bug: the LP
    runs out of one token side and every subsequent deposit fails."""
    legacy = _run(swap_enabled=False)

    assert legacy.failed_deposits >= 5, (
        f"legacy path should accumulate failed deposits in the quiet "
        f"phase; got only {legacy.failed_deposits}"
    )
    assert legacy.swaps_emitted == 0, (
        "legacy path must never emit a rebalance swap"
    )
    assert not legacy.has_position_at_end, (
        "legacy path should leave the LP stranded with no position"
    )


def test_fix_strictly_outperforms_legacy_on_redeposit_recovery() -> None:
    """Side-by-side: fix has zero failed deposits and a live position;
    legacy has many failures and no position. This is the chart-cliff
    regression in measurable form."""
    legacy = _run(swap_enabled=False)
    fixed = _run(swap_enabled=True)

    assert fixed.failed_deposits < legacy.failed_deposits
    assert fixed.has_position_at_end and not legacy.has_position_at_end
