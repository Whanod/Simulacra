"""Tests for ``PassiveLP`` / ``RebalancingLP`` decision logic.

Focus: the post-IL-exit recovery path. After a CLMM withdraw, the LP's
two-token balance is heavily skewed toward whichever side the price
drifted into. Without a rebalance step, the bootstrap-deposit branch
either deposits a meaningless sliver or skips entirely, leaving the LP
permanently parked — that's the "Liquidity Over Time cliff" the user
flagged. ``swap_to_balance_on_redeposit`` (default ``True``) inserts a
``SwapAction`` ahead of the deposit so both tokens have value at mint
time.
"""

from __future__ import annotations

from defi_sim.agents.lp import LPParams, PassiveLP, RebalancingLP
from defi_sim.core.agent import DecisionContext
from defi_sim.core.types import (
    AgentState,
    AmmSnapshot,
    LPAction,
    LPActionType,
    SwapAction,
)


def _ctx(
    *,
    balances: dict[str, int],
    prices: dict[str, float],
    decimals: dict[str, int],
    reserves: dict[str, int] | None = None,
    lp_position=None,
    unrealized_loss: float = 0.0,
) -> DecisionContext:
    state = AgentState(agent_id="lp1", balances=dict(balances))
    # Default reserves to a deep pool so the rebalance helper's
    # ``max_pool_fraction`` cap doesn't bite tests that aren't about it.
    snap_reserves = (
        dict(reserves)
        if reserves is not None
        else {tok: 10**18 for tok in prices.keys()}
    )
    snap = AmmSnapshot(
        num_assets=len(prices),
        tokens=list(prices.keys()),
        prices=dict(prices),
        reserves=snap_reserves,
    )
    return DecisionContext(
        market_state=snap,
        agent_state=state,
        extra={
            "lp_position": lp_position,
            "unrealized_loss": unrealized_loss,
            "token_decimals": dict(decimals),
        },
    )


def _make_position(deposited: int = 1) -> object:
    """Tiny stand-in for an LPPosition with a non-zero ``deposited``."""
    class _P:
        pass
    p = _P()
    p.deposited = deposited
    return p


def _seed_position_history(agent: PassiveLP | RebalancingLP) -> None:
    """Walk the agent through one round where it currently holds a
    position, so the sticky ``_has_held_position`` flag flips on. The
    next call (with no position) is then the post-withdraw state."""
    seed = _ctx(
        balances={"A": 1_000_000, "B": 1_000_000},
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
        lp_position=_make_position(deposited=1_000_000),
    )
    agent.decide(seed)


def test_passive_lp_emits_swap_only_when_skewed_then_deposits_next_round() -> None:
    """Post-withdraw with ~zero collateral, the LP defers its deposit.

    Models the bug from the chart: an LP deposits, price drifts up past
    the upper tick, IL trips, the position withdraws as ~100% token A,
    leaving collateral (B) near zero. The fix emits a SwapAction A→B
    *alone* on the first round; the next round (with rebalanced
    balances) the agent's normal bootstrap deposit fires.

    The split is intentional — bundling swap+deposit in one round causes
    the deposit's range to be computed against pre-swap spot, which a
    large rebalance swap can push outside, dropping L to zero.

    The fix is also gated on having previously held a position (sticky
    ``_has_held_position`` flag), so fresh single-token first-deposits
    aren't disrupted by an unnecessary swap that would lock the pool
    against bundles or competing agents.
    """
    agent = PassiveLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    _seed_position_history(agent)
    ctx_skewed = _ctx(
        balances={"A": 10_000_000, "B": 100},  # 10 A vs 0.0001 B (raw, both 6 dec)
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )

    actions = agent.decide(ctx_skewed)

    assert len(actions) == 1, f"expected swap-only, got {actions}"
    swap = actions[0]
    assert isinstance(swap, SwapAction)
    assert swap.token_in == "A"
    assert swap.token_out == "B"
    # Should swap roughly half the value (target 50/50): ~5M raw of A.
    assert 4_000_000 <= int(swap.amount_in) <= 6_000_000

    # Simulate the swap landing: balances are now near-balanced. Next
    # decide() should fire the deposit normally.
    ctx_after = _ctx(
        balances={"A": 5_000_000, "B": 5_000_000},
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )
    next_actions = agent.decide(ctx_after)
    assert len(next_actions) == 1
    assert isinstance(next_actions[0], LPAction)
    assert next_actions[0].lp_type == LPActionType.DEPOSIT
    assert int(next_actions[0].amount) > 0


def test_passive_lp_first_deposit_with_single_token_does_not_swap() -> None:
    """Regression: an LP with only the collateral token (no prior
    position) must NOT emit a swap. CFAMM-style single-token deposits
    are routine, and emitting an unsolicited swap on round 0 conflicts
    with bundles or other agents that lock the same pool account
    (caught by ``test_solana_template_with_bundles_runs_end_to_end``)."""
    agent = PassiveLP("lp1", LPParams(collateral="USDC", deposit_fraction=0.5))
    ctx = _ctx(
        balances={"USDC": 2_000_000_000, "SOL": 0},  # only collateral, like the template
        prices={"USDC": 1.0, "SOL": 100.0},
        decimals={"USDC": 6, "SOL": 9},
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    assert isinstance(actions[0], LPAction)
    assert actions[0].lp_type == LPActionType.DEPOSIT


def test_passive_lp_skips_swap_when_balances_already_balanced() -> None:
    """If both tokens are already near 50/50 by value, no swap is needed."""
    agent = PassiveLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    _seed_position_history(agent)
    ctx = _ctx(
        balances={"A": 1_000_000, "B": 1_000_000},  # 50/50 at price 1.0
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    assert isinstance(actions[0], LPAction)
    assert actions[0].lp_type == LPActionType.DEPOSIT


def test_passive_lp_legacy_path_when_swap_disabled() -> None:
    """``swap_to_balance_on_redeposit=False`` restores pre-fix behavior."""
    agent = PassiveLP(
        "lp1",
        LPParams(
            collateral="B",
            deposit_fraction=0.5,
            swap_to_balance_on_redeposit=False,
        ),
    )
    _seed_position_history(agent)
    # Pre-fix: with collateral B near zero, no deposit fires (ever).
    ctx = _ctx(
        balances={"A": 10_000_000, "B": 0},
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )

    assert agent.decide(ctx) == []


def test_passive_lp_swaps_other_direction_when_overweight_collateral() -> None:
    """If the LP holds ~100% collateral and 0 of the other token, the
    centered-range deposit would compute zero L on the A side. The
    rebalance helper should swap B→A (alone, deferring the deposit).
    Requires a prior position so the post-withdraw gate fires."""
    agent = PassiveLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    _seed_position_history(agent)
    ctx = _ctx(
        balances={"A": 0, "B": 10_000_000},
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    swap = actions[0]
    assert isinstance(swap, SwapAction)
    assert swap.token_in == "B"
    assert swap.token_out == "A"


def test_rebalancing_lp_also_rebalances_before_redeposit() -> None:
    """Same fix applies to RebalancingLP's bootstrap branch."""
    agent = RebalancingLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    _seed_position_history(agent)
    ctx = _ctx(
        balances={"A": 10_000_000, "B": 100},
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    assert isinstance(actions[0], SwapAction)


def test_rebalancing_lp_on_clmm_emits_withdraw_at_periodic_interval() -> None:
    """On a CLMM (``supports_lp_rebalance=False``, e.g. Whirlpool) the
    ``LPActionType.REBALANCE`` shape isn't accepted by the market.
    Instead, the periodic timer issues a ``WITHDRAW`` so the bootstrap
    branch can re-mint a centered range on the new spot next round.

    Without this, ``RebalancingLP`` silently degrades to ``PassiveLP``
    on Whirlpool — confirmed by the user reporting a flat agent-L
    line in the Total LP Deposits chart even with rebalance_interval
    set."""
    agent = RebalancingLP(
        "lp1",
        LPParams(collateral="B", deposit_fraction=0.5, rebalance_interval=10),
    )
    # Simulate "agent has a position, IL is fine, periodic timer hits."
    state = AgentState(agent_id="lp1", balances={"A": 5_000_000, "B": 5_000_000})
    snap = AmmSnapshot(
        num_assets=2,
        tokens=["A", "B"],
        prices={"A": 1.0, "B": 1.0},
        reserves={"A": 10**12, "B": 10**12},
    )
    ctx = DecisionContext(
        market_state=snap,
        agent_state=state,
        current_round=10,  # divisible by rebalance_interval
        extra={
            "lp_position": _make_position(deposited=1_000_000),
            "unrealized_loss": 0.0,
            "token_decimals": {"A": 6, "B": 6},
            "supports_lp_rebalance": False,  # Whirlpool / CLMM
        },
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, LPAction)
    assert action.lp_type == LPActionType.WITHDRAW


def test_rebalancing_lp_on_cfamm_still_emits_rebalance_at_periodic_interval() -> None:
    """On a CFAMM the legacy ``REBALANCE`` action with target weights
    is still emitted — this preserves backwards compatibility for the
    pools that natively support a single-action weight reset."""
    agent = RebalancingLP(
        "lp1",
        LPParams(collateral="B", deposit_fraction=0.5, rebalance_interval=10),
    )
    state = AgentState(agent_id="lp1", balances={"A": 5_000_000, "B": 5_000_000})
    snap = AmmSnapshot(
        num_assets=2,
        tokens=["A", "B"],
        prices={"A": 1.0, "B": 1.0},
    )
    ctx = DecisionContext(
        market_state=snap,
        agent_state=state,
        current_round=10,
        extra={
            "lp_position": _make_position(deposited=1_000_000),
            "unrealized_loss": 0.0,
            "supports_lp_rebalance": True,  # CFAMM
        },
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, LPAction)
    assert action.lp_type == LPActionType.REBALANCE
    assert action.target_weights is not None and len(action.target_weights) == 2


def test_rebalance_swap_capped_by_pool_reserves() -> None:
    """In thin pools, the swap is capped at 5% of the input-side
    reserve so price impact stays bounded. The agent will rebalance
    over multiple rounds rather than oscillating from one giant swap."""
    agent = PassiveLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    _seed_position_history(agent)
    ctx = _ctx(
        balances={"A": 10_000_000_000, "B": 100},  # huge A imbalance
        prices={"A": 1.0, "B": 1.0},
        decimals={"A": 6, "B": 6},
        reserves={"A": 1_000_000_000, "B": 1_000_000_000},  # thin vs LP
    )

    actions = agent.decide(ctx)

    assert len(actions) == 1
    swap = actions[0]
    assert isinstance(swap, SwapAction)
    assert swap.token_in == "A"
    # Without the cap the helper would swap ~5B; with 5% cap on a 1B
    # reserve, it should bound to 50M.
    assert int(swap.amount_in) <= 50_000_000


def test_rebalance_helper_no_op_without_token_decimals() -> None:
    """Without ``token_decimals`` in ctx.extra the helper bails — we
    can't price across tokens. The legacy bootstrap path runs instead."""
    agent = PassiveLP("lp1", LPParams(collateral="B", deposit_fraction=0.5))
    state = AgentState(agent_id="lp1", balances={"A": 10_000_000, "B": 1_000_000})
    snap = AmmSnapshot(
        num_assets=2,
        tokens=["A", "B"],
        prices={"A": 1.0, "B": 1.0},
    )
    ctx = DecisionContext(
        market_state=snap,
        agent_state=state,
        extra={"lp_position": None, "unrealized_loss": 0.0},
    )

    actions = agent.decide(ctx)

    # No swap (decimals missing); deposit still fires from balance(B).
    assert len(actions) == 1
    assert isinstance(actions[0], LPAction)
