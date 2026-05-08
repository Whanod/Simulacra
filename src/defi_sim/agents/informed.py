"""Informed trader agent — trades toward a belief distribution."""

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
    TokenId,
)


@dataclass
class InformedParams:
    collateral: TokenId = "COLLATERAL"
    conviction: float = 0.5
    trade_fraction: float = 0.1
    capital_limit: Numeric = 1_000_000_000_000


class InformedTrader(Agent):
    """Trades toward a reference distribution from ctx.belief.
    Conviction is a flat configurable float."""

    def __init__(self, agent_id: AgentId, params: InformedParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or InformedParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("informed"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if ctx.belief is None or ctx.market_state is None:
            return []

        balance = ctx.agent_state.balance(self.params.collateral)
        if balance <= 0:
            return []

        # Compute trade amount
        if isinstance(balance, float):
            trade_amount = min(balance * self.params.trade_fraction * self.params.conviction,
                               float(self.params.capital_limit))
        else:
            trade_amount = min(
                int(balance * self.params.trade_fraction * self.params.conviction),
                int(self.params.capital_limit),
            )

        if trade_amount <= 0:
            return []

        tokens = ctx.market_state.tokens
        if not tokens:
            return []

        # Use belief as weights for a bundle action
        weights: dict[TokenId, Numeric] = {}
        total_belief = sum(ctx.belief.get(t, 0) for t in tokens)
        if total_belief <= 0:
            return []

        for t in tokens:
            weights[t] = ctx.belief.get(t, 0)

        return [BundleAction(
            agent_id=self.agent_id,
            collateral=self.params.collateral,
            amount=trade_amount,
            weights=weights,
        )]
