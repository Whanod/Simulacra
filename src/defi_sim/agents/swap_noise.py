"""Swap-flavored noise trader — emits ``SwapAction`` for a configured pair.

PRD Phase 1.5 lighthouse template needs a built-in agent that produces
``SwapAction`` victims so ``JitoSearcher.run_sandwich`` (and ``run_backrun``)
have something to detect: those strategies filter ``ctx.pending_actions``
strictly by ``isinstance(action, SwapAction)``, and the existing
:class:`NoiseTrader` / :class:`Manipulator` agents emit
``SingleAssetAction`` / ``BundleAction`` only.

Per-slot the agent samples a fire decision against ``frequency``, draws a
random amount in ``[amount_min, amount_max]`` (clamped to balance), and
draws a random ``compute_unit_price_micro_lamports`` so the
``PriorityFeeMarket`` percentiles shift across slots — without varying
priority fees, the fee-market percentile distribution stays at 0 and no
``PRIORITY_FEE_MARKET_UPDATED`` events fire.
"""

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
    SwapAction,
    TokenId,
)


@dataclass
class SwapNoiseParams:
    token_in: TokenId = ""
    token_out: TokenId = ""
    amount_min: Numeric = 1_000_000
    amount_max: Numeric = 10_000_000
    frequency: float = 0.5
    cu_price_min: int = 1_000
    cu_price_max: int = 50_000
    # Fee-elasticity of trade size — see NoiseParams for full semantics.
    # Disabled (0.0) by default; set to ~1.0 in the lighthouse template
    # so halving the Whirlpool fee tier (e.g. 4 bps → 2 bps) drives a
    # ~2× uplift in per-decision swap notional. Template callers override
    # ``reference_fee_bps`` to the pool's captured on-chain fee_rate.
    fee_elasticity: float = 0.0
    reference_fee_bps: float = 30.0


class SwapNoiseTrader(Agent):
    def __init__(
        self,
        agent_id: AgentId,
        params: SwapNoiseParams | None = None,
        rng: np.random.Generator | None = None,
    ):
        self.agent_id = agent_id
        self.params = params or SwapNoiseParams()
        self._rng = rng or np.random.default_rng(hash(agent_id) % (2**31))
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("swap_noise"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if not self.params.token_in or not self.params.token_out:
            return []
        if self._rng.random() > self.params.frequency:
            return []
        balance = ctx.agent_state.balance(self.params.token_in)
        amount_min = int(self.params.amount_min)
        if int(balance) <= amount_min:
            return []
        size_scale = self._fee_size_scale(ctx)
        scaled_max = int(self.params.amount_max * size_scale)
        amount_max = min(scaled_max, int(balance))
        if amount_max <= amount_min:
            return []
        amount = int(self._rng.integers(amount_min, amount_max + 1))
        cu_price = int(
            self._rng.integers(
                self.params.cu_price_min, self.params.cu_price_max + 1
            )
        )
        action = SwapAction(
            agent_id=self.agent_id,
            token_in=self.params.token_in,
            token_out=self.params.token_out,
            amount_in=amount,
            compute_unit_price_micro_lamports=cu_price,
        )
        return [action]

    def _fee_size_scale(self, ctx: DecisionContext) -> float:
        """``(reference_fee_bps / fee_bps) ** fee_elasticity`` — see NoiseTrader."""
        if self.params.fee_elasticity == 0.0:
            return 1.0
        fee_bps = getattr(ctx.market_state, "fee_bps", None)
        if fee_bps is None or fee_bps <= 0:
            return 1.0
        ref = float(self.params.reference_fee_bps)
        if ref <= 0:
            return 1.0
        return float((ref / float(fee_bps)) ** self.params.fee_elasticity)
