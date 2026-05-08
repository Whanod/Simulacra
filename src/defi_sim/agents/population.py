"""Population builder for creating agent populations from config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from defi_sim.core.agent import Agent
from defi_sim.core.types import Numeric, Token


@dataclass
class PopulationConfig:
    """Declarative agent population specification."""
    mix: dict[str, float]
    total_agents: int = 100
    default_collateral: Numeric = 10_000_000_000_000  # 10k in 9-decimal scale

    role_params: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        total = sum(self.mix.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"mix fractions must sum to 1.0, got {total}")


class PopulationBuilder:
    """Creates agent populations from PopulationConfig."""

    _factories: dict[str, Callable[..., Agent]] = {}

    @classmethod
    def register(cls, role: str, factory: Callable[..., Agent]) -> None:
        """Register a factory function for a role name."""
        cls._factories[role] = factory

    @classmethod
    def build(
        cls,
        config: PopulationConfig,
        collateral_token: Token,
        rng: np.random.Generator | None = None,
    ) -> list[Agent]:
        """Create agents according to config."""
        if rng is None:
            rng = np.random.default_rng(42)

        agents: list[Agent] = []
        agent_id = 0
        raw_counts = {
            role: config.total_agents * fraction
            for role, fraction in config.mix.items()
        }
        role_counts = {
            role: int(count)
            for role, count in raw_counts.items()
        }
        assigned = sum(role_counts.values())
        if assigned < config.total_agents:
            remainders = sorted(
                raw_counts.items(),
                key=lambda item: item[1] - int(item[1]),
                reverse=True,
            )
            for role, _ in remainders[: config.total_agents - assigned]:
                role_counts[role] += 1

        for role, fraction in config.mix.items():
            count = role_counts.get(role, 0)
            factory = cls._factories.get(role)
            if factory is None:
                raise ValueError(f"No factory registered for role '{role}'. "
                                 f"Available: {list(cls._factories.keys())}")

            role_params = config.role_params.get(role, {})

            for _ in range(count):
                agent_rng = np.random.default_rng(rng.integers(0, 2**31))
                agent = factory(agent_id=agent_id, rng=agent_rng, **role_params)

                # Set initial collateral
                if not agent.state.balances:
                    agent.state.balances = {}
                agent.state.balances[collateral_token.id] = config.default_collateral

                agents.append(agent)
                agent_id += 1

        return agents


# Pre-register built-in agent factories
def _register_builtins() -> None:
    from defi_sim.agents.noise import NoiseTrader
    from defi_sim.agents.noise import NoiseParams
    from defi_sim.agents.informed import InformedTrader
    from defi_sim.agents.informed import InformedParams
    from defi_sim.agents.arbitrageur import Arbitrageur
    from defi_sim.agents.arbitrageur import ArbitrageParams
    from defi_sim.agents.manipulator import Manipulator
    from defi_sim.agents.manipulator import ManipulatorParams
    from defi_sim.agents.lp import PassiveLP, RebalancingLP
    from defi_sim.agents.lp import LPParams

    PopulationBuilder.register(
        "noise",
        lambda agent_id, rng=None, **kw: NoiseTrader(agent_id, params=NoiseParams(**kw) if kw else None, rng=rng),
    )
    PopulationBuilder.register(
        "informed",
        lambda agent_id, rng=None, **kw: InformedTrader(agent_id, params=InformedParams(**kw) if kw else None, rng=rng),
    )
    PopulationBuilder.register(
        "arbitrageur",
        lambda agent_id, rng=None, **kw: Arbitrageur(agent_id, params=ArbitrageParams(**kw) if kw else None, rng=rng),
    )
    PopulationBuilder.register(
        "manipulator",
        lambda agent_id, rng=None, **kw: Manipulator(agent_id, params=ManipulatorParams(**kw) if kw else None, rng=rng),
    )
    PopulationBuilder.register(
        "lp",
        lambda agent_id, rng=None, **kw: PassiveLP(agent_id, params=LPParams(**kw) if kw else None, rng=rng),
    )
    PopulationBuilder.register(
        "rebalancing_lp",
        lambda agent_id, rng=None, **kw: RebalancingLP(agent_id, params=LPParams(**kw) if kw else None, rng=rng),
    )


_register_builtins()
