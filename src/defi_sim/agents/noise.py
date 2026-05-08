"""Noise trader agent — random uninformed trades."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentRole,
    AgentState,
    BundleAction,
    Numeric,
    Side,
    SingleAssetAction,
    TokenId,
)
from defi_sim.utils.distributions import gaussian_weights


@dataclass
class NoiseParams:
    collateral: TokenId = "COLLATERAL"
    trade_min: Numeric = 100
    trade_max: Numeric = 1000
    frequency: float = 0.5
    bundle_probability: float = 0.3
    # When True, single-asset emissions split 50/50 between Side.BUY
    # (spend collateral, receive asset) and Side.SELL (spend asset,
    # receive collateral). Default False preserves the historical
    # buy-only behavior so existing fixtures don't drift. Bundle
    # emissions remain BUY-only because BundleAction is a
    # collateral→weighted-basket primitive with no inverse.
    # ``trade_min`` / ``trade_max`` are interpreted as raw units of
    # whichever token the agent is *spending* on this emission, so on
    # SELL the budget is the chosen asset's balance — not its
    # collateral-equivalent notional. Sizing trade_min/max for a
    # mixed-decimal pool may need different magnitudes per token.
    bidirectional: bool = False
    # Fee-elasticity of trade size. Each decision multiplies trade_min /
    # trade_max by ``(reference_fee_bps / current_fee_bps) ** fee_elasticity``,
    # where ``current_fee_bps`` is read from the live ``AmmSnapshot.fee_bps``.
    # Default 0.0 disables scaling so existing fixtures and non-AMM markets
    # are unaffected. Markets that don't surface ``fee_bps`` (snapshot field
    # missing or None) also leave sizes untouched. Use elasticity ≈ 1.0 to
    # roughly model "lower fees attract proportionally more flow" — useful
    # for protocol-design counterfactuals on Whirlpool fee tiers.
    fee_elasticity: float = 0.0
    reference_fee_bps: float = 30.0


class NoiseTrader(Agent):
    """Random uninformed trader. Buys single assets or bundles."""

    def __init__(self, agent_id: AgentId, params: NoiseParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or NoiseParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("noise"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if self._rng.random() > self.params.frequency:
            return []
        if ctx.market_state is None or not ctx.market_state.tokens:
            return []

        tokens = ctx.market_state.tokens
        size_scale = self._fee_size_scale(ctx)

        # Bundle path is BUY-only — BundleAction is a collateral→basket
        # primitive with no inverse, so bidirectional doesn't apply here.
        # Roll the bundle decision first so the bundle/single split stays
        # at ``bundle_probability`` regardless of which side a single
        # emission ends up on.
        if self._rng.random() < self.params.bundle_probability and len(tokens) > 1:
            balance = ctx.agent_state.balance(self.params.collateral)
            if balance <= self.params.trade_min:
                return []
            amount = self._draw_amount(balance, size_scale)
            if amount is None:
                return []
            mu = self._rng.uniform(0, len(tokens))
            sigma = self._rng.uniform(1, len(tokens) / 2)
            scale = ctx.extra.get("weight_scale", 10**9)
            if isinstance(scale, float):
                positions = np.arange(len(tokens)) + 0.5
                raw = np.exp(-0.5 * ((positions - mu) / sigma) ** 2)
                raw_sum = raw.sum()
                weights = {
                    tokens[i]: float(raw[i] / raw_sum) if raw_sum > 0 else 1.0 / len(tokens)
                    for i in range(len(tokens))
                }
            else:
                weights_arr = gaussian_weights(len(tokens), mu, sigma, normalize_to=scale)
                weights = {tokens[i]: int(weights_arr[i]) for i in range(len(tokens))}
            return [BundleAction(
                agent_id=self.agent_id,
                collateral=self.params.collateral,
                amount=amount,
                weights=weights,
                mu=mu,
                sigma=sigma,
            )]

        # Single asset trade — bidirectional flips this 50/50 between
        # BUY (spend collateral) and SELL (spend the chosen asset).
        side = Side.BUY
        if self.params.bidirectional and self._rng.random() < 0.5:
            side = Side.SELL

        if side == Side.SELL:
            # SELL needs an asset != collateral, otherwise the action
            # is a no-op pair. Restrict the token pool here so SELL
            # emissions don't silently drop ~50% of the time on a
            # 2-token pool (which would re-skew flow toward BUY).
            sellable = [t for t in tokens if t != self.params.collateral]
            if not sellable:
                return []
            token = self._rng.choice(sellable)
        else:
            token = self._rng.choice(tokens)

        spend_token = self.params.collateral if side == Side.BUY else token
        balance = ctx.agent_state.balance(spend_token)
        if balance <= self.params.trade_min:
            return []
        amount = self._draw_amount(balance, size_scale)
        if amount is None:
            return []

        return [SingleAssetAction(
            agent_id=self.agent_id,
            asset=token,
            collateral=self.params.collateral,
            amount=amount,
            side=side,
        )]

    def _draw_amount(self, balance: Numeric, size_scale: float = 1.0) -> Numeric | None:
        # ``size_scale`` is the fee-elasticity multiplier (default 1.0 = off).
        # We scale the *upper* bound only — the lower bound is left intact so
        # a small-fee world doesn't accidentally raise the floor of trade
        # sizes and crowd out small flow. Balance still clamps the upper
        # bound; agents can't spend more than they hold.
        if isinstance(self.params.trade_min, float):
            scaled_max = float(self.params.trade_max) * size_scale
            upper = min(scaled_max, float(balance))
            lower = float(self.params.trade_min)
            if upper <= lower:
                return None
            return self._rng.uniform(lower, upper)
        scaled_max = int(self.params.trade_max * size_scale)
        max_amt = min(scaled_max, int(balance))
        min_amt = int(self.params.trade_min)
        if max_amt <= min_amt:
            return None
        return int(self._rng.integers(min_amt, max_amt))

    def _fee_size_scale(self, ctx: DecisionContext) -> float:
        """Compute the fee-elasticity multiplier from snapshot ``fee_bps``.

        Returns 1.0 when elasticity is zero, the snapshot lacks a stable
        fee surface, or the fee is non-positive — i.e. all the cases where
        scaling would either be a no-op or undefined. Otherwise returns
        ``(reference_fee_bps / current_fee_bps) ** fee_elasticity`` so a
        lower-fee market produces proportionally larger trades.
        """
        if self.params.fee_elasticity == 0.0:
            return 1.0
        fee_bps = getattr(ctx.market_state, "fee_bps", None)
        if fee_bps is None or fee_bps <= 0:
            return 1.0
        ref = float(self.params.reference_fee_bps)
        if ref <= 0:
            return 1.0
        return float((ref / float(fee_bps)) ** self.params.fee_elasticity)
