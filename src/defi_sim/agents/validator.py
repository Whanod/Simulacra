"""Validator agent — receives leader slots by stake weight and accrues bundle tips.

PRD US-012: Validators do not trade. They observe leader slots and accrue rewards
from bundle tips (Jito-Solana clients) or block rewards only (vanilla clients).
Tip routing to validator vs. JitoSOL stake-pool is wired in a later sub-task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentRole,
    AgentState,
)


@dataclass(kw_only=True)
class ValidatorParams:
    # PRD US-012 line 956: validator pubkey is the on-chain identity and
    # has no default — production construction must always supply one.
    # ``defaults_for_dataclass`` reads field defaults directly without
    # instantiating, so the missing default doesn't break introspection.
    pubkey: str
    client: Literal["jito_solana", "vanilla"] = "jito_solana"
    stake_pool_share: float = 0.05
    stake_pool_address: AgentId | None = None
    stake_lamports: int = 0
    commission_pct: float = 0.05

    def __post_init__(self) -> None:
        if not self.pubkey:
            raise ValueError("ValidatorParams.pubkey must be a non-empty string")


class Validator(Agent):
    """Validator agent: passive — does not emit actions."""

    def __init__(self, agent_id: AgentId, params: ValidatorParams):
        self.agent_id = agent_id
        self.params = params
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("validator"),
        )

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []
