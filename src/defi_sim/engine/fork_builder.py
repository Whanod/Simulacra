"""``build_forked_engine`` (PRD US-003 line 717).

Wires :class:`ForkLoader` -> :func:`materialize_fork` -> :class:`SimulationEngine`
so the engine receives a hydrated ``World`` rather than a stateful execution
model. Forward simulation runs synthetic agents on the hydrated world exactly
like any other run; the engine reads state from ``self._market`` and is
unaware that the world was forked.

The helper deliberately keeps the wiring linear and side-effect-free: load,
materialize, seed, swap in a :class:`ForkExecution` preset, construct.
``ForkExecution`` carries only ``start_slot`` (PRD line 716 invariant) so the
engine remains the single owner of mutable protocol state.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from defi_sim.engine.fork_execution import ForkExecution
from defi_sim.engine.fork_hydration import materialize_fork
from defi_sim.engine.forkable import SeedableAgent
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.simulation import SimulationEngine

if TYPE_CHECKING:
    from defi_sim.core.agent import Agent
    from defi_sim.engine.config import SimulationConfig
    from defi_sim.engine.fork import ForkSpec
    from defi_sim.engine.fork_hydration import AgentStateSeed
    from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry

__all__ = ["build_forked_engine"]


def build_forked_engine(
    fork_spec: "ForkSpec",
    fork_loader: "ForkLoader",
    registry: "ProtocolModelRegistry",
    agents: list["Agent"],
    config: "SimulationConfig",
) -> SimulationEngine:
    """Build a runnable :class:`SimulationEngine` over forked mainnet state.

    Steps: ``fork_loader.load(fork_spec)`` produces a cacheable
    :class:`InitialState`; :func:`materialize_fork` turns that into a
    :class:`HydratedFork` (``World`` + per-owner agent seeds + price feeds);
    seedable agents receive their owner-keyed fragments; the resulting
    :class:`SimulationEngine` runs forward with whatever synthetic agents the
    caller passed — no historical actions are replayed.
    """
    initial = fork_loader.load(fork_spec)
    hydrated = materialize_fork(
        initial,
        registry,
        parameters=config.parameters or ParameterStore(),
        numeric_mode=config.numeric_mode,
    )
    _seed_agents(agents, hydrated.agent_seeds)
    forked_config = replace(
        config,
        execution_model=ForkExecution(start_slot=hydrated.start_slot),
    )
    return SimulationEngine(
        market=hydrated.world,
        agents=agents,
        config=forked_config,
    )


def _seed_agents(
    agents: list["Agent"],
    agent_seeds: dict[str, "AgentStateSeed"],
) -> None:
    """Dispatch owner-keyed fragments to matching :class:`SeedableAgent`s.

    Non-seedable agents and seedable agents whose ``agent_id`` does not match
    a fragment owner are left untouched. The materializer already groups
    fragments by owner; this helper only routes them.
    """
    for agent in agents:
        if not isinstance(agent, SeedableAgent):
            continue
        seed = agent_seeds.get(agent.agent_id)
        if seed is None:
            continue
        agent.seed_from_fragments(list(seed.fragments))
