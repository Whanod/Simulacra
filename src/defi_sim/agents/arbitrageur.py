"""Arbitrageur agent — exploits mispricings against feed prices or sum heuristics."""

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
    Numeric,
    Side,
    SingleAssetAction,
    TokenId,
)


@dataclass
class ArbitrageParams:
    collateral: TokenId = "COLLATERAL"
    min_edge_bps: int = 50
    trade_fraction: float = 0.2
    max_trade: Numeric = 1_000_000_000_000


class Arbitrageur(Agent):
    """Generic mispricing detector. Compares market prices against
    ctx.feed_prices when available, falls back to price-sum heuristics for AMMs."""

    def __init__(self, agent_id: AgentId, params: ArbitrageParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or ArbitrageParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("arbitrageur"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if ctx.market_state is None:
            return []

        actions: list[Action] = []
        balance = ctx.agent_state.balance(self.params.collateral)
        if balance <= 0:
            return []

        tokens = ctx.market_state.tokens

        # Get market prices
        market_prices: dict[TokenId, Numeric] = {}
        if isinstance(ctx.market_state, AmmSnapshot):
            market_prices = ctx.market_state.prices

        if not market_prices:
            return []

        # Compare against feed prices if available
        if ctx.feed_prices:
            for token in tokens:
                mkt_price = market_prices.get(token, 0)
                feed_price = ctx.feed_prices.get(token, 0)
                if mkt_price <= 0 or feed_price <= 0:
                    continue

                # Compute edge in bps
                if isinstance(mkt_price, float):
                    edge_bps = abs(feed_price - mkt_price) / mkt_price * 10000
                else:
                    edge_bps = abs(feed_price - mkt_price) * 10000 // mkt_price

                if edge_bps >= self.params.min_edge_bps:
                    if isinstance(balance, float):
                        trade_amt = min(balance * self.params.trade_fraction, float(self.params.max_trade))
                    else:
                        trade_amt = min(int(balance * self.params.trade_fraction), int(self.params.max_trade))

                    side = Side.BUY if feed_price > mkt_price else Side.SELL
                    actions.append(SingleAssetAction(
                        agent_id=self.agent_id,
                        asset=token,
                        collateral=self.params.collateral,
                        amount=trade_amt,
                        side=side,
                    ))
                    break  # One arb per round

        # Fallback: AMM price-sum heuristic
        elif isinstance(ctx.market_state, AmmSnapshot) and not actions:
            # If prices sum to more or less than scale, there's an arb
            scale = ctx.extra.get("price_scale", 10**9)
            price_sum = sum(market_prices.values())

            if isinstance(price_sum, float):
                target = float(scale)
                deviation = abs(price_sum - target)
                if deviation > self.params.min_edge_bps / 10000:
                    # Find most mispriced token
                    fair = target / len(tokens) if tokens else 0
                    token = max(tokens, key=lambda t: abs(market_prices.get(t, 0) - fair))
                    trade_amt = min(balance * self.params.trade_fraction, float(self.params.max_trade))
                    side = Side.BUY if market_prices.get(token, 0) < fair else Side.SELL
                    actions.append(SingleAssetAction(
                        agent_id=self.agent_id, asset=token,
                        collateral=self.params.collateral, amount=trade_amt, side=side))
            else:
                deviation_bps = abs(price_sum - scale) * 10000 // scale if scale > 0 else 0
                if deviation_bps > self.params.min_edge_bps:
                    fair = scale // len(tokens) if tokens else 0
                    token = max(tokens, key=lambda t: abs(market_prices.get(t, 0) - fair))
                    trade_amt = min(int(balance * self.params.trade_fraction), int(self.params.max_trade))
                    side = Side.BUY if market_prices.get(token, 0) < fair else Side.SELL
                    actions.append(SingleAssetAction(
                        agent_id=self.agent_id, asset=token,
                        collateral=self.params.collateral, amount=trade_amt, side=side))

        return actions
