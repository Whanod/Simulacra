"""LP agents — passive and rebalancing liquidity providers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentRole,
    AgentState,
    AmmSnapshot,
    LPAction,
    LPActionType,
    Numeric,
    SwapAction,
    TokenId,
)


def _context_fee_yield(ctx: DecisionContext) -> float:
    return float(ctx.extra.get("fee_yield", 0.0))


def _context_unrealized_loss(ctx: DecisionContext) -> float:
    return float(ctx.extra.get("unrealized_loss", 0.0))


def _lp_deposited_amount(ctx: DecisionContext) -> Numeric:
    lp_position = ctx.extra.get("lp_position")
    if lp_position is None:
        return 0
    return lp_position.deposited


def _resolve_lp_price_range(
    params: "LPParams", ctx: DecisionContext
) -> tuple[Numeric, Numeric] | None:
    """Compute the price-range tuple this LP wants for its next mint.

    Returns ``None`` to mean full-range. ``symmetric_pct`` reads the
    primary token's spot price out of ``ctx.market_state`` (the
    AmmSnapshot already exposes priced markets) and centers a
    ±``range_width_pct`` band around it.
    """
    mode = (params.range_mode or "full_range").lower()
    if mode == "full_range":
        return None
    if mode == "ticks":
        if params.tick_lower is None or params.tick_upper is None:
            return None
        return (float(params.tick_lower), float(params.tick_upper))
    if mode == "symmetric_pct":
        if not isinstance(ctx.market_state, AmmSnapshot):
            return None
        prices = ctx.market_state.prices or {}
        # Pick the non-collateral token's price as the centering spot.
        spot = None
        for token, price in prices.items():
            if token == params.collateral:
                continue
            try:
                p = float(price)
            except (TypeError, ValueError):
                continue
            if p > 0:
                spot = p
                break
        if spot is None:
            return None
        width = max(0.0, float(params.range_width_pct))
        return (spot * (1.0 - width), spot * (1.0 + width))
    return None


def _position_in_range(ctx: DecisionContext) -> bool | None:
    """Surface ``in_range`` from a ConcentratedLPPosition when present."""
    pos = ctx.extra.get("lp_position")
    if pos is None:
        return None
    return getattr(pos, "in_range", None)


def _rebalance_for_deposit(
    ctx: DecisionContext,
    agent_id: AgentId,
    collateral: TokenId,
    target_collateral_fraction: float = 0.5,
    min_side_fraction: float = 0.10,
    max_pool_fraction: float = 0.05,
) -> list[Action]:
    """Compute swap actions needed before redepositing into a 2-token AMM.

    Specifically targets the *stranded-LP* case: after an IL-triggered
    withdraw of a CLMM position, the LP holds near-100% of one token
    (whichever side the price drifted into) and near-zero of the other.
    A centered-range re-mint then computes zero liquidity and the LP
    sits idle forever.

    The trigger is intentionally conservative — a swap fires only when
    one side holds less than ``min_side_fraction`` of total value
    (default 10%). Ordinary first-deposit cases with balanced or
    mildly-skewed holdings fall through unchanged so the LP doesn't
    introduce a competing lock against bundles or other agents on the
    pool account in normal operation.

    The swap is emitted *alone*: the caller defers its deposit to the
    next round so the deposit's price-range (resolved against the live
    spot at decide time) reflects the post-swap state. Trying to bundle
    swap+deposit in a single round is fragile because a large rebalance
    swap can push price outside the deposit's planned tick range,
    causing the market to round liquidity down to zero.

    ``max_pool_fraction`` caps the swap input at a fraction of the
    pool's reserve on the input side (when ``ctx.market_state.reserves``
    is populated). This keeps price impact bounded — without it, an LP
    whose balance dwarfs the ambient pool oscillates as each rebalance
    move overshoots. The cap means a single rebalance may not fully
    flatten the imbalance; the agent simply emits another swap on the
    next round until the trigger no longer fires.
    """
    if not isinstance(ctx.market_state, AmmSnapshot):
        return []
    prices = ctx.market_state.prices or {}
    if collateral not in prices:
        return []

    other_token: TokenId | None = None
    for tok, price in prices.items():
        if tok == collateral:
            continue
        try:
            if float(price) > 0:
                other_token = tok
                break
        except (TypeError, ValueError):
            continue
    if other_token is None:
        return []

    decimals = ctx.extra.get("token_decimals", {})
    dec_collateral = decimals.get(collateral)
    dec_other = decimals.get(other_token)
    if dec_collateral is None or dec_other is None:
        return []

    bal_collateral = ctx.agent_state.balance(collateral)
    bal_other = ctx.agent_state.balance(other_token)
    p_collateral = float(prices[collateral])
    p_other = float(prices[other_token])
    if p_collateral <= 0 or p_other <= 0:
        return []

    val_collateral = (float(bal_collateral) / (10 ** int(dec_collateral))) * p_collateral
    val_other = (float(bal_other) / (10 ** int(dec_other))) * p_other
    total_value = val_collateral + val_other
    if total_value <= 0:
        return []

    # Only intervene when one side is near-empty by value. Ordinary
    # off-50/50 deposits go through unchanged.
    val_min = min(val_collateral, val_other)
    if val_min >= total_value * min_side_fraction:
        return []

    target_value = total_value * target_collateral_fraction
    delta_value = target_value - val_collateral
    if abs(delta_value) <= 0:
        return []

    reserves = getattr(ctx.market_state, "reserves", {}) or {}

    if delta_value > 0:
        # Need more collateral — swap other → collateral.
        amount_in_other = int((delta_value / p_other) * (10 ** int(dec_other)))
        amount_in_other = min(amount_in_other, int(bal_other))
        reserve_cap = int(float(reserves.get(other_token, 0)) * max_pool_fraction)
        if reserve_cap > 0:
            amount_in_other = min(amount_in_other, reserve_cap)
        if amount_in_other <= 0:
            return []
        return [SwapAction(
            agent_id=agent_id,
            token_in=other_token,
            token_out=collateral,
            amount_in=amount_in_other,
        )]

    # delta_value < 0: too much collateral — swap collateral → other.
    amount_in_collateral = int((abs(delta_value) / p_collateral) * (10 ** int(dec_collateral)))
    amount_in_collateral = min(amount_in_collateral, int(bal_collateral))
    reserve_cap = int(float(reserves.get(collateral, 0)) * max_pool_fraction)
    if reserve_cap > 0:
        amount_in_collateral = min(amount_in_collateral, reserve_cap)
    if amount_in_collateral <= 0:
        return []
    return [SwapAction(
        agent_id=agent_id,
        token_in=collateral,
        token_out=other_token,
        amount_in=amount_in_collateral,
    )]


@dataclass
class LPParams:
    collateral: TokenId = "COLLATERAL"
    min_yield_per_round: float = 0.001
    max_loss_threshold: float = 0.05
    deposit_fraction: float = 0.5
    rebalance_interval: int = 10
    # Range-aware (CLMM) parameters. ``range_mode`` selects how the LP
    # picks its tick range each time it deposits:
    #   ``full_range``     — pass ``price_range=None`` (V2-style mint).
    #   ``symmetric_pct``  — center on the spot, span ±range_width_pct.
    #   ``ticks``          — use explicit ``tick_lower`` / ``tick_upper``
    #                        (interpreted as raw tick indices and resolved
    #                        to prices by the market).
    # Pre-Phase-3.1.2 markets (CFAMM) ignore these fields; they only
    # take effect on markets that honor ``LPAction.price_range``.
    range_mode: str = "full_range"
    range_width_pct: float = 0.05
    tick_lower: int | None = None
    tick_upper: int | None = None
    # When ``True``, exit (withdraw) as soon as the spot drifts out of
    # the LP's range so the agent can re-mint a centered range on the
    # next decide tick.
    rebalance_on_exit: bool = False
    # When ``True`` (default), the bootstrap-deposit branch precedes the
    # ``DEPOSIT`` with a ``SwapAction`` that pulls the agent's two-token
    # holdings back toward a balanced split. Without this, an LP that
    # withdraws after an IL exit (when its position has settled mostly
    # into one token) cannot re-mint a meaningful position because the
    # collateral side is depleted. Disable for legacy single-shot
    # behavior or for non-AMM markets where the swap doesn't apply.
    swap_to_balance_on_redeposit: bool = True


class PassiveLP(Agent):
    """Deposits if yield > threshold, withdraws if loss > threshold."""

    def __init__(self, agent_id: AgentId, params: LPParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or LPParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self._deposited = False
        # Sticky flag: True once a position has *ever* existed for this
        # agent. Gates the rebalance-before-redeposit path so the swap
        # only fires for the post-IL-exit "stranded on one token"
        # scenario, not for an LP's very first deposit (where holding
        # only the collateral side is normal and most market types
        # accept it without intervention).
        self._has_held_position = False
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("lp"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        balance = ctx.agent_state.balance(self.params.collateral)
        deposited = _lp_deposited_amount(ctx)
        unrealized_loss = _context_unrealized_loss(ctx)
        in_range = _position_in_range(ctx)
        # Re-derive deposit state from the market each round. The Solana
        # ``submission_path_drop`` Bernoulli (PRD US-004) silently drops
        # ~5% of RPC actions, so a previously-issued DEPOSIT may never
        # have landed. Trusting an internal ``_deposited`` flag instead
        # of the market's view leaves the LP permanently stuck.
        self._deposited = deposited > 0
        if self._deposited:
            self._has_held_position = True

        # Withdraw if either (a) IL exceeded the loss threshold, or (b)
        # the LP is configured to rebalance on exit and the spot drifted
        # out of the active tick range.
        should_exit_on_il = (
            unrealized_loss > self.params.max_loss_threshold and deposited > 0
        )
        should_exit_on_drift = (
            self.params.rebalance_on_exit
            and deposited > 0
            and in_range is False
        )
        if should_exit_on_il or should_exit_on_drift:
            self._deposited = False
            return [LPAction(
                agent_id=self.agent_id,
                collateral=self.params.collateral,
                amount=deposited,
                lp_type=LPActionType.WITHDRAW,
            )]

        # Bootstrap deposit. The ``min_yield_per_round`` gate previously
        # lived here, but ``fee_yield`` reflects the LP's *own* realized
        # yield — zero until a position exists — so the gate deadlocked
        # every passive LP. Engine-side ``ctx.extra`` doesn't yet carry a
        # pool-level fee-yield estimate; until it does, we bootstrap on
        # the first round with a positive balance and let
        # ``max_loss_threshold`` handle exits.
        if not self._deposited:
            if (
                self.params.swap_to_balance_on_redeposit
                and self._has_held_position
            ):
                rebalance_actions = _rebalance_for_deposit(
                    ctx, self.agent_id, self.params.collateral
                )
                if rebalance_actions:
                    # Defer the deposit by one round. The next decide
                    # tick observes the post-swap balances + spot and the
                    # deposit's range computes against the updated price.
                    return rebalance_actions
            if balance > 0:
                if isinstance(balance, float):
                    deposit_amt = balance * self.params.deposit_fraction
                else:
                    deposit_amt = int(balance * self.params.deposit_fraction)
                if deposit_amt > 0:
                    self._deposited = True
                    return [LPAction(
                        agent_id=self.agent_id,
                        collateral=self.params.collateral,
                        amount=deposit_amt,
                        lp_type=LPActionType.DEPOSIT,
                        price_range=_resolve_lp_price_range(self.params, ctx),
                    )]

        return []


class RebalancingLP(Agent):
    """Like PassiveLP but rebalances every N rounds."""

    def __init__(self, agent_id: AgentId, params: LPParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or LPParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self._deposited = False
        self._deposit_amount: Numeric = 0
        # See PassiveLP for the rationale on this sticky flag.
        self._has_held_position = False
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("lp"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        balance = ctx.agent_state.balance(self.params.collateral)
        deposited = _lp_deposited_amount(ctx)
        unrealized_loss = _context_unrealized_loss(ctx)
        # See PassiveLP.decide for why ``_deposited`` is re-derived here.
        self._deposited = deposited > 0
        if self._deposited:
            self._has_held_position = True

        if unrealized_loss > self.params.max_loss_threshold and deposited > 0:
            self._deposited = False
            self._deposit_amount = 0
            return [LPAction(
                agent_id=self.agent_id,
                collateral=self.params.collateral,
                amount=deposited,
                lp_type=LPActionType.WITHDRAW,
            )]

        # See PassiveLP.decide for why the ``min_yield_per_round`` gate is
        # not checked on bootstrap, and for why a rebalance swap defers
        # the deposit by one round.
        if not self._deposited:
            if (
                self.params.swap_to_balance_on_redeposit
                and self._has_held_position
            ):
                rebalance_actions = _rebalance_for_deposit(
                    ctx, self.agent_id, self.params.collateral
                )
                if rebalance_actions:
                    return rebalance_actions
            if balance > 0:
                if isinstance(balance, float):
                    deposit_amt = balance * self.params.deposit_fraction
                else:
                    deposit_amt = int(balance * self.params.deposit_fraction)
                if deposit_amt > 0:
                    self._deposited = True
                    self._deposit_amount = deposit_amt
                    return [LPAction(
                        agent_id=self.agent_id,
                        collateral=self.params.collateral,
                        amount=deposit_amt,
                        lp_type=LPActionType.DEPOSIT,
                        price_range=_resolve_lp_price_range(self.params, ctx),
                    )]

        # Periodic rebalance.
        #
        # CFAMM (``supports_lp_rebalance=True``) accepts a single
        # ``REBALANCE`` action that resets reserve weights without
        # withdrawing — the legacy path. CLMM markets like Whirlpool
        # don't have a weight-reset semantics; the equivalent is to
        # burn the existing position and re-mint at a fresh range
        # centered on the current spot. We do that as a withdraw here
        # and let the bootstrap-deposit branch re-mint on the next
        # decide tick (the deferred path is mandatory anyway when the
        # rebalance helper needs to swap to balance, see
        # ``_rebalance_for_deposit``).
        #
        # Without this CLMM branch, ``RebalancingLP`` degrades to
        # ``PassiveLP`` behavior on Whirlpool (the rebalance gate
        # never fires) — pre-fix it produced a flat agent-L line in
        # the Total LP Deposits chart, identical to passive_lp.
        if (
            self._deposited
            and ctx.current_round > 0
            and ctx.current_round % self.params.rebalance_interval == 0
        ):
            if ctx.extra.get("supports_lp_rebalance", False):
                if ctx.market_state and isinstance(ctx.market_state, AmmSnapshot):
                    tokens = ctx.market_state.tokens
                    if tokens:
                        num = len(tokens)
                        scale = ctx.extra.get("weight_scale", 10**9)
                        if isinstance(scale, float):
                            weight_per = scale / num
                            target = {t: weight_per for t in tokens}
                        else:
                            weight_per = scale // num
                            target = {t: weight_per for t in tokens}
                            remainder = scale - weight_per * num
                            if remainder > 0:
                                target[tokens[0]] += remainder

                        return [LPAction(
                            agent_id=self.agent_id,
                            collateral=self.params.collateral,
                            amount=0,
                            lp_type=LPActionType.REBALANCE,
                            target_weights=target,
                        )]
            else:
                # CLMM: withdraw the current position. Next decide tick
                # the bootstrap branch re-mints centered on the new spot.
                self._deposited = False
                self._deposit_amount = 0
                return [LPAction(
                    agent_id=self.agent_id,
                    collateral=self.params.collateral,
                    amount=deposited,
                    lp_type=LPActionType.WITHDRAW,
                )]

        return []
