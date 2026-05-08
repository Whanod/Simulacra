"""Manipulator agent — price distortion and volume wash strategies."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentRole,
    AgentState,
    Numeric,
    Side,
    SingleAssetAction,
    TokenId,
)


@dataclass
class ManipulatorParams:
    collateral: TokenId = "COLLATERAL"
    strategy: str = "price_distortion"  # "price_distortion" or "volume_wash"
    target_token: TokenId | None = None
    budget: Numeric = 5_000_000_000_000
    num_tranches: int = 10
    spend_fraction: float = 0.1


class Manipulator(Agent):
    """Two generic strategies:
    - price_distortion: push a target asset's price
    - volume_wash: inflate volume for fee tier gaming
    """

    def __init__(self, agent_id: AgentId, params: ManipulatorParams | None = None,
                 rng: np.random.Generator | None = None):
        self.agent_id = agent_id
        self.params = params or ManipulatorParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self._spent: Numeric = 0
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("manipulator"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if ctx.market_state is None:
            return []

        balance = ctx.agent_state.balance(self.params.collateral)
        if balance <= 0:
            return []

        # Check budget
        if isinstance(self._spent, float):
            if self._spent >= float(self.params.budget):
                return []
        else:
            if self._spent >= int(self.params.budget):
                return []

        tokens = ctx.market_state.tokens
        if not tokens:
            return []

        if self.params.strategy == "price_distortion":
            return self._price_distortion(ctx, tokens, balance)
        elif self.params.strategy == "volume_wash":
            return self._volume_wash(ctx, tokens, balance)
        return []

    def _price_distortion(self, ctx: DecisionContext, tokens: list[TokenId],
                          balance: Numeric) -> list[Action]:
        """Aggressive single-token buying to push price."""
        target = self.params.target_token or tokens[0]
        if target not in tokens:
            target = tokens[0]

        # Tranche amount
        if isinstance(self.params.budget, float):
            tranche = float(self.params.budget) / self.params.num_tranches
            amount = min(tranche, float(balance) * self.params.spend_fraction)
        else:
            tranche = int(self.params.budget) // self.params.num_tranches
            amount = min(tranche, int(int(balance) * self.params.spend_fraction))

        if amount <= 0:
            return []

        self._spent = self._spent + amount
        return [SingleAssetAction(
            agent_id=self.agent_id,
            asset=target,
            collateral=self.params.collateral,
            amount=amount,
            side=Side.BUY,
        )]

    def _volume_wash(self, ctx: DecisionContext, tokens: list[TokenId],
                     balance: Numeric) -> list[Action]:
        """Buy and sell to inflate volume (for fee tier gaming)."""
        token = self._rng.choice(tokens)

        if isinstance(balance, float):
            amount = float(balance) * self.params.spend_fraction * 0.1
        else:
            amount = int(int(balance) * self.params.spend_fraction * 0.1)

        if amount <= 0:
            return []

        # Buy action (the sell happens naturally next round when agent has tokens)
        self._spent = self._spent + amount
        return [SingleAssetAction(
            agent_id=self.agent_id,
            asset=token,
            collateral=self.params.collateral,
            amount=amount,
            side=Side.BUY,
        )]
