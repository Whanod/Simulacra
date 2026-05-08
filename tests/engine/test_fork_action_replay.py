"""Engine-level fork action-replay coverage (PRD US-014 line 1120).

A fork reorg of depth ``d`` at slot ``N`` abandons state transitions for
slots ``[N-d, N]`` and the next slot must be rebuilt by replaying admitted
regular actions for those slots in their original order. Jito ``Bundle``
objects from abandoned slots are NOT replayed (they revert).

This file pins the engine-side replay queue in
``SimulationEngine._fork_admitted_actions`` → ``_deferred_carryover`` flow.
"""

from __future__ import annotations

import copy
import random
from collections import deque

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import (
    Action,
    AgentState,
    BundleAction,
    Side,
    SwapAction,
)
from defi_sim.engine.api import build_engine
from defi_sim.engine.fork import ChainReorgForkSpec


SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "placeholder",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 5,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "fifo"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


class FixedAgent(Agent):
    """Emits one scripted action on slot 0 only, then nothing."""

    def __init__(self, agent_id: str, action: Action | None = None) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._fired = False
        self._action = action

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if self._fired:
            return []
        self._fired = True
        if self._action is not None:
            return [self._action]
        return [
            SwapAction(
                agent_id=self.agent_id,
                token_in="USDC",
                token_out="SOL",
                amount_in=10,
                compute_unit_limit=200_000,
            )
        ]


def _build_engine_with_always_fork(depth: int = 1) -> object:
    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)
    agent = FixedAgent("placeholder")
    agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
    engine._agents = [agent]
    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=depth,
        seed=42,
    )
    execution = engine._execution_model
    execution._fork_spec = fork_spec
    execution._fork_rng = random.Random(fork_spec.seed)
    execution._slot_history = deque(maxlen=fork_spec.max_reorg_depth_slots + 1)
    return engine


def test_admitted_non_bundle_actions_replay_after_fork_abandons_slot():
    """After a fork abandons a slot's admitted actions, those actions are
    queued onto ``_deferred_carryover`` so the next slot replays them in
    original order (PRD line 1120).
    """
    engine = _build_engine_with_always_fork(depth=1)

    # Slot 1: the agent's swap is admitted and executed; the depth-1 fork
    # at this slot abandons it; the engine queues the action for replay.
    engine.step()
    queued = list(engine._deferred_carryover)
    assert len(queued) == 1
    assert isinstance(queued[0], SwapAction)
    assert queued[0].token_in == "USDC"
    assert queued[0].token_out == "SOL"

    # Slot 2: the carryover swap re-admits and executes; another fork
    # abandons it; one swap is queued for slot 3.
    engine.step()
    queued = list(engine._deferred_carryover)
    assert len(queued) == 1
    assert isinstance(queued[0], SwapAction)


def test_weighted_bundle_actions_replay_after_fork():
    """The core ``BundleAction`` is a normal weighted basket trade, not a Jito
    bundle object. It must replay after a fork like every other admitted
    regular action.
    """
    action = BundleAction(
        agent_id="placeholder",
        collateral="USDC",
        amount=1,
        weights={"SOL": 1.0},
        side=Side.BUY,
    )
    engine = _build_engine_with_always_fork(depth=1)
    agent = FixedAgent("placeholder", action)
    agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
    engine._agents = [agent]

    engine.step()

    queued = list(engine._deferred_carryover)
    assert len(queued) == 1
    assert isinstance(queued[0], BundleAction)
