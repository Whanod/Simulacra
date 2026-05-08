"""Engine-level deferred-action carryover (PRD US-002 line 128 / line 167).

When an action is deferred at the per-slot CU enforcement stage,
``SimulationEngine`` must re-queue it onto the next slot's
``pending_actions`` so the agent does NOT need to resubmit the same action.
This file pins that behaviour from end-to-end: a single CU-heavy action
emitted at slot N executes at slot N+1 even when the agent stops emitting.
"""

from __future__ import annotations

import copy

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import Action, AgentState, SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.compute_budget import ComputeBudget
from defi_sim.engine.execution import SolanaLikeExecution


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
    "num_rounds": 2,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "fifo"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


class TwoActionThenIdle(Agent):
    """Emits two CU-heavy swaps on the first slot it sees, then nothing.

    With a per-slot CU cap that admits only the first swap, the second
    must defer onto the next slot's pending list. Because the agent stops
    emitting after the first slot, the executed action in slot 2 can only
    have come from the engine-level carryover.
    """

    def __init__(self, agent_id: str, cu_limit: int) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._cu_limit = cu_limit
        self._fired = False

    def decide(self, ctx: DecisionContext) -> list[Action]:
        if self._fired:
            return []
        self._fired = True
        return [
            SwapAction(
                agent_id=self.agent_id,
                token_in="USDC",
                token_out="SOL",
                amount_in=10,
                compute_unit_limit=self._cu_limit,
            ),
            SwapAction(
                agent_id=self.agent_id,
                token_in="USDC",
                token_out="SOL",
                amount_in=10,
                compute_unit_limit=self._cu_limit,
            ),
        ]


def _build_engine_with_emitter(cu_limit: int, *, per_slot: int) -> object:
    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)
    # Reuse the placeholder agent_id so the engine's RNG registry already
    # has an entry for the swapped-in agent.
    agent = TwoActionThenIdle("placeholder", cu_limit=cu_limit)
    agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
    engine._agents = [agent]
    engine._execution_model = SolanaLikeExecution(
        cost_token="USDC",
        compute_budget=ComputeBudget(
            per_slot=per_slot,
            per_tx=cu_limit + 1,
            per_writable_account=2 * cu_limit + 1,
        ),
    )
    return engine


def test_per_slot_overflow_action_executes_in_next_slot_without_resubmit() -> None:
    # Two 300_000-CU actions emitted in slot 1; a 500_000 per-slot cap
    # admits the first and forces the second to defer. The agent emits
    # nothing in slot 2, so any execution there must come from the
    # engine's carryover queue.
    engine = _build_engine_with_emitter(cu_limit=300_000, per_slot=500_000)
    snap1 = engine.step()
    bal_slot1 = snap1.agent_states["placeholder"].balances["USDC"]
    # First action landed in slot 1 (balance moved); deferred action sits
    # on the engine-level carryover queue waiting for slot 2.
    assert bal_slot1 < 1_000_000_000
    assert len(engine._deferred_carryover) == 1

    snap2 = engine.step()
    bal_slot2 = snap2.agent_states["placeholder"].balances["USDC"]
    # Slot 2: the deferred action executed even though the agent fired
    # nothing this round. Carryover queue is empty.
    assert bal_slot2 < bal_slot1
    assert engine._deferred_carryover == []
