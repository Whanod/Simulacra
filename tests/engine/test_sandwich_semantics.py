"""PRD US-003 sandwich-semantics integration tests.

These tests exercise the full Solana slot pipeline (admit → resolve_locks
→ scheduler → execute) with scripted agents that emit sandwich-shaped
swaps. They pin the contract that within a single ``ParallelLane`` the
``PriorityScheduler`` orders actions by ``scheduler_priority_score``
descending — so a higher ``compute_unit_price_micro_lamports`` lands
ahead of a lower-priced one when every other priority input is held
constant.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Sequence

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import Action, AgentState, MultiMarketAction, SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.events import EventType
from defi_sim.engine.scheduler import LockedAction, ParallelLane, PriorityScheduler


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
            "agent_id": "front-run",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "victim",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "back-run",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


class _PricedSwapEmitter(Agent):
    """Emits a single same-pool USDC→SOL swap each round at a configured CU price.

    All three sandwich agents share ``num_required_signatures`` (default
    1), ``compute_unit_limit`` (default), write-lock count (one — the
    cfamm pool), and trade size — so ``scheduler_priority_score`` differs
    only via ``compute_unit_price_micro_lamports``.
    """

    def __init__(self, agent_id: str, cu_price: int) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._cu_price = cu_price

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return [
            SwapAction(
                agent_id=self.agent_id,
                token_in="USDC",
                token_out="SOL",
                amount_in=10,
                compute_unit_price_micro_lamports=self._cu_price,
            )
        ]


def test_sandwich_cu_price_breaks_tie_within_lane() -> None:
    """PRD US-003 lines 294 / 316: three actions on the same pool with
    different ``compute_unit_price_micro_lamports`` (every other priority
    input identical) execute in ``scheduler_priority_score`` descending
    order within the single shared lane.

    All three swaps target the same cfamm pool, so the lock resolver
    emits ``write_locks={pool_account}`` for every action; the conflict
    graph is a triangle and ``PriorityScheduler`` packs them into one
    lane sorted by score. With identical signature counts, write-lock
    counts, and CU limits, the score is monotonic in the priority fee:
    front-run (10_000) > victim (5_000) > back-run (1_000).

    The assertion is on ACTION_EXECUTED order, which reflects the
    scheduler's lane order rather than agent-emission order.
    """
    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)

    # Replace the noise stubs with deterministic emitters at the same
    # agent_ids (so engine._agent_rngs lookups still resolve).
    front = _PricedSwapEmitter("front-run", cu_price=10_000)
    victim = _PricedSwapEmitter("victim", cu_price=5_000)
    back = _PricedSwapEmitter("back-run", cu_price=1_000)
    for agent in (front, victim, back):
        agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}

    # Insertion order is intentionally NOT score-sorted: victim first,
    # then back-run, then front-run. A scheduler that preserved input
    # order — or sorted ascending — would fail.
    engine._agents = [victim, back, front]

    executed: list[str] = []

    def listener(evt) -> None:
        executed.append(evt.data.get("agent_id"))

    engine._bus.on(EventType.ACTION_EXECUTED, listener)
    engine.run()

    assert executed == ["front-run", "victim", "back-run"], executed


def _run_sandwich_once(spec: dict) -> list[tuple[int, str]]:
    """Build a fresh engine from ``spec``, replace the noise stubs with
    deterministic priced emitters at front-run > victim > back-run CU
    prices, run one slot, and return the list of ``(round, agent_id)``
    pairs in the order the engine emitted ``ACTION_EXECUTED``.
    """
    engine = build_engine(copy.deepcopy(spec))
    front = _PricedSwapEmitter("front-run", cu_price=10_000)
    victim = _PricedSwapEmitter("victim", cu_price=5_000)
    back = _PricedSwapEmitter("back-run", cu_price=1_000)
    for agent in (front, victim, back):
        agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
    # Insertion order is intentionally NOT score-sorted.
    engine._agents = [victim, back, front]

    executed: list[tuple[int, str]] = []

    def listener(evt) -> None:
        executed.append((evt.round, evt.data.get("agent_id")))

    engine._bus.on(EventType.ACTION_EXECUTED, listener)
    engine.run()
    return executed


def test_sandwich_same_pool_landed_in_correct_order() -> None:
    """PRD US-003 line 314 + spec at line 287: front-run, victim, and
    back-run all on the same single-pool ``Whirlpool/SOL/USDC`` cfamm
    market land in the **same slot** within a **single ParallelLane**,
    and ``front_run.execution_order < victim.execution_order``
    deterministically given the same seed.

    This complements ``test_sandwich_cu_price_breaks_tie_within_lane``
    (line 316), which pins the score-ordering tie-breaker contract.
    Here we additionally assert (a) all three actions land in the same
    slot — verifying same-slot landing as required by PRD line 287 —
    and (b) re-running with the identical seed produces the identical
    sequence, locking in the determinism guarantee for the seeded
    ``PriorityScheduler`` path.
    """
    # Label the spec with a Whirlpool/SOL/USDC name to match the PRD's
    # framing, even though the underlying market type is cfamm until
    # the dedicated Whirlpool model lands in Phase 2.3b / 3.1.2.
    spec = copy.deepcopy(SOLANA_SPEC)
    spec["market"]["name"] = "Whirlpool/SOL/USDC"

    first = _run_sandwich_once(spec)
    second = _run_sandwich_once(spec)

    assert first == second, (first, second)

    agents_in_order = [agent_id for _, agent_id in first]
    assert agents_in_order == ["front-run", "victim", "back-run"], agents_in_order

    front_idx = agents_in_order.index("front-run")
    victim_idx = agents_in_order.index("victim")
    assert front_idx < victim_idx, (front_idx, victim_idx)

    # All three sandwich legs landed in the same slot (single-lane,
    # single-round assertion).
    rounds = {round_num for round_num, _ in first}
    assert len(rounds) == 1, rounds


# --- sandwich on different pools -----------------------------------------


class _ShuffledLaneScheduler(PriorityScheduler):
    """``PriorityScheduler`` wrapped with a seeded inter-lane shuffle.

    ``schedule()`` first builds the lock-conflict-graph lanes via the
    base implementation (preserving within-lane priority sort) and then
    shuffles the lane list using a per-instance ``random.Random``. Used
    by ``test_sandwich_different_pools_no_ordering_guarantee`` to drive
    1000 different inter-lane orderings while keeping every other slice
    of the pipeline (admission, lock resolution, within-lane sort)
    unchanged.
    """

    def __init__(self, seed: int) -> None:
        super().__init__()
        self._rng = random.Random(seed)

    def schedule(
        self,
        actions: Sequence[LockedAction],
        slot: int,
        state: Any = None,
    ) -> list[ParallelLane]:
        lanes = super().schedule(actions, slot, state=state)
        self._rng.shuffle(lanes)
        return lanes


WORLD_SPEC: dict = {
    "market": {
        "type": "world",
        "markets": {
            "pool_a": {
                "type": "cfamm",
                "tokens": [
                    {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                    {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                ],
                "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
            },
            "pool_b": {
                "type": "cfamm",
                "tokens": [
                    {"id": "BONK", "symbol": "BONK", "decimals": 5, "standard": "spl"},
                    {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                ],
                "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
            },
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "front-run",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000, "BONK": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "victim",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000, "BONK": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


class _PoolEmitter(Agent):
    """Emits a single MultiMarketAction-wrapped USDC swap on a target pool.

    Used by ``test_sandwich_different_pools_no_ordering_guarantee`` so
    each agent's swap routes to a distinct cfamm market — whose lock
    resolver emits a distinct ``write_locks={pool_account}``, putting the
    two swaps into different ``ParallelLane``s of the conflict graph.
    """

    def __init__(self, agent_id: str, market_name: str, token_in: str, token_out: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._market_name = market_name
        self._token_in = token_in
        self._token_out = token_out

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return [
            MultiMarketAction(
                agent_id=self.agent_id,
                market_name=self._market_name,
                inner=SwapAction(
                    agent_id=self.agent_id,
                    token_in=self._token_in,
                    token_out=self._token_out,
                    amount_in=1000,
                ),
            )
        ]


def test_sandwich_different_pools_no_ordering_guarantee() -> None:
    """PRD US-003 lines 295 / 315 + spec at line 288: when the front-run
    targets ``pool_a`` and the victim targets ``pool_b``, the conflict
    graph has two disconnected components (one per pool). The
    ``PriorityScheduler`` emits two independent ``ParallelLane``s and
    inter-lane order is undefined under the parallel-execution contract.

    The test wraps ``PriorityScheduler`` in ``_ShuffledLaneScheduler``,
    which preserves the (correct) within-lane sort but randomly permutes
    the returned lane list per a seeded RNG. Over ``NUM_TRIALS`` distinct
    seeds the front-run executes before the victim ``0.50 ± 0.05`` of the
    time — verifying the scheduler does NOT preserve the sandwich's
    intended ordering when there is no lock conflict between the two
    legs. Standard error of a Bernoulli(0.5) sum at N=1000 is ~0.0158, so
    the ±0.05 band is ~3σ wide and the assertion is robust.
    """
    NUM_TRIALS = 1000
    front_first_count = 0
    starting_balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000, "BONK": 1_000_000_000}

    for trial_seed in range(NUM_TRIALS):
        engine = build_engine(copy.deepcopy(WORLD_SPEC))
        front = _PoolEmitter("front-run", "pool_a", "USDC", "SOL")
        victim = _PoolEmitter("victim", "pool_b", "USDC", "BONK")
        for agent in (front, victim):
            agent.state.balances = dict(starting_balances)
        engine._agents = [front, victim]
        engine._execution_model._scheduler = _ShuffledLaneScheduler(seed=trial_seed)

        executed: list[str] = []

        def listener(evt) -> None:
            executed.append(evt.data.get("agent_id"))

        engine._bus.on(EventType.ACTION_EXECUTED, listener)
        engine.run()

        # Both legs must land — otherwise the trial doesn't tell us
        # anything about inter-lane ordering. Defensive assertion to
        # surface admission / lock-resolver regressions early.
        assert "front-run" in executed and "victim" in executed, (trial_seed, executed)
        if executed.index("front-run") < executed.index("victim"):
            front_first_count += 1

    fraction = front_first_count / NUM_TRIALS
    assert 0.45 <= fraction <= 0.55, (front_first_count, fraction)
