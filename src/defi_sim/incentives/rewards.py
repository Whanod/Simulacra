"""Reward distribution strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from defi_sim.core.market import LiquidityPool, Market
from defi_sim.core.math import mul_fp
from defi_sim.core.types import AgentId, AgentState, Numeric, TokenId


class RewardDistributor(ABC):
    """Distributes emitted reward tokens to eligible agents."""

    @abstractmethod
    def distribute(
        self,
        rewards: dict[TokenId, Numeric],
        markets: list[Market],
        agent_states: dict[AgentId, AgentState],
    ) -> dict[AgentId, dict[TokenId, Numeric]]: ...


class ProRataLPDistributor(RewardDistributor):
    """Distribute rewards proportionally to LP share fractions."""

    def distribute(self, rewards, markets, agent_states):
        from defi_sim.core.market import LPPosition

        all_positions: list[LPPosition] = []
        for market in markets:
            if isinstance(market, LiquidityPool):
                all_positions.extend(market.get_all_lp_positions())

        total_shares = sum(p.share_fraction for p in all_positions)
        if total_shares == 0:
            return {}

        result: dict[AgentId, dict[TokenId, Numeric]] = {}
        for pos in all_positions:
            agent_rewards: dict[TokenId, Numeric] = {}
            for token, amount in rewards.items():
                if isinstance(amount, float):
                    share = amount * pos.share_fraction / total_shares
                else:
                    share = mul_fp(amount, pos.share_fraction, total_shares)
                agent_rewards[token] = share

            if pos.agent_id in result:
                for token, amount in agent_rewards.items():
                    result[pos.agent_id][token] = result[pos.agent_id].get(token, 0) + amount
            else:
                result[pos.agent_id] = agent_rewards

        return result


class StakeWeightedDistributor(RewardDistributor):
    """Distribute rewards proportionally to staked balances."""

    def __init__(self, stake_token: TokenId):
        self._stake_token = stake_token

    def distribute(self, rewards, markets, agent_states):
        total_staked = sum(
            s.balance(self._stake_token) for s in agent_states.values()
        )
        if total_staked <= 0:
            return {}

        result: dict[AgentId, dict[TokenId, Numeric]] = {}
        for agent_id, state in agent_states.items():
            staked = state.balance(self._stake_token)
            if staked <= 0:
                continue

            agent_rewards: dict[TokenId, Numeric] = {}
            for token, amount in rewards.items():
                if isinstance(amount, float):
                    share = amount * staked / total_staked
                else:
                    share = mul_fp(amount, staked, total_staked)
                agent_rewards[token] = share

            result[agent_id] = agent_rewards

        return result


class CustomDistributor(RewardDistributor):
    """User-defined distribution logic."""

    def __init__(self, fn: Callable):
        self._fn = fn

    def distribute(self, rewards, markets, agent_states):
        return self._fn(rewards, markets, agent_states)
