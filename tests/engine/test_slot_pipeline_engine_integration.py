"""Engine-level integration tests for the slot pipeline (PRD Phase 1.0).

The regression bar is bit-identical behaviour: a Solana spec run with
``supports_slot_execution()`` returning False (legacy admit/order branch)
must produce the same per-action outcomes as the real True branch.
"""

from __future__ import annotations

import copy

from defi_sim.engine.api import build_engine
from defi_sim.engine.events import EventType
from defi_sim.engine.execution import (
    BatchExecution,
    SolanaLikeExecution,
)
from defi_sim.engine.simulation import SimulationEngine


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
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "noise-2",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 4,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
        # cost_token defaults to COLLATERAL; pin to USDC so the noise agent
        # can actually pay the priority fee and we exercise ACTION_EXECUTED.
        "params": {"cost_token": "USDC"},
    },
}


def _round_trace(engine: SimulationEngine):
    """Reduce a run to a comparable trace of (round, agent_id, balances)."""
    result = engine.run()
    trace = []
    for snap in result.round_snapshots:
        for agent_id, agent_state in sorted(snap.agent_states.items()):
            trace.append((snap.round, agent_id, dict(agent_state.balances)))
    return trace


_ACTION_EVENT_TYPES = {
    EventType.ACTION_EXECUTED,
    EventType.ACTION_FAILED,
    EventType.ACTION_DROPPED,
}


def _capture_action_events(engine: SimulationEngine) -> list[tuple]:
    """Subscribe to action events and return a per-event trace."""
    captured: list[tuple] = []

    def listener(evt) -> None:
        action = evt.data.get("action")
        captured.append((
            evt.round,
            evt.type.name,
            action.__class__.__name__ if action is not None else None,
            evt.data.get("agent_id"),
            evt.data.get("market_name"),
        ))

    for et in _ACTION_EVENT_TYPES:
        engine._bus.on(et, listener)
    return captured


def _run_and_collect(engine: SimulationEngine):
    events = _capture_action_events(engine)
    trace = _round_trace(engine)
    return trace, events


def test_solana_like_execute_slot_matches_legacy_pipeline_output() -> None:
    """Same spec/seed: legacy admit/order path vs. slot pipeline produce identical
    balance traces, identical action event streams, and identical per-action
    succeeded flags. PRD AC line 216: bit-identical event stream and per-action
    succeeded flags."""
    spec_a = copy.deepcopy(SOLANA_SPEC)
    spec_b = copy.deepcopy(SOLANA_SPEC)

    engine_slot = build_engine(spec_a)
    assert engine_slot._execution_model.supports_slot_execution() is True

    engine_legacy = build_engine(spec_b)
    # Force the legacy branch on this engine without touching the model class globally.
    engine_legacy._execution_model.supports_slot_execution = lambda: False

    slot_trace, slot_events = _run_and_collect(engine_slot)
    legacy_trace, legacy_events = _run_and_collect(engine_legacy)

    assert slot_trace == legacy_trace
    # Action event stream (round, type, action class, agent, market) must match.
    assert slot_events == legacy_events
    # And there must actually be EXECUTED events to compare; otherwise this
    # asserts emptiness against emptiness.
    executed = [e for e in slot_events if e[1] == "ACTION_EXECUTED"]
    assert executed, "expected at least one ACTION_EXECUTED in the trace"


def test_executor_marks_succeeded_false_when_protocol_fails() -> None:
    """Issue 1 regression: the slot executor must report succeeded=False when
    market.execute() fails, not blanket True."""
    from defi_sim.core.types import ExecutionResult, SwapAction

    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)

    # Force every market.execute() to return a protocol failure.
    def always_fail(action, ctx):
        return ExecutionResult(success=False, error="forced failure")

    engine._market.execute = always_fail  # type: ignore[method-assign]

    actions = [
        SwapAction(agent_id="noise-1", token_in="USDC", token_out="SOL", amount_in=1),
        SwapAction(agent_id="noise-2", token_in="USDC", token_out="SOL", amount_in=1),
    ]
    executor = engine._action_executor_for_slot(round_num=0, ts=0)
    results = [executor(a, 0) for a in actions]

    assert all(r.succeeded is False for r in results), results
    assert all(r.failure_reason == "forced failure" for r in results), results


def test_chain_neutral_batch_execution_uses_legacy_path() -> None:
    """A non-Solana BatchExecution never invokes execute_slot()."""
    invocations = {"count": 0}

    class TrackingBatch(BatchExecution):
        def supports_slot_execution(self) -> bool:
            return False

        def execute_slot(self, ctx):  # pragma: no cover - guarded by assertion
            invocations["count"] += 1
            return super().execute_slot(ctx)

    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)
    engine._execution_model = TrackingBatch()
    engine.run()
    assert invocations["count"] == 0


def test_decision_context_exposes_current_slot_before_execute_slot() -> None:
    """Agents decide before ``execute_slot`` sets the execution model's
    internal current slot, so the context must be populated from the round
    being built rather than from stale model state.
    """
    import numpy as np

    from defi_sim.core.agent import Agent, DecisionContext
    from defi_sim.core.types import Action, AgentState
    from defi_sim.engine.leader_schedule import LeaderSchedule, ValidatorStake

    class CapturingAgent(Agent):
        def __init__(self) -> None:
            self.agent_id = "capture"
            self.state = AgentState(agent_id=self.agent_id)
            self.contexts: list[DecisionContext] = []

        def decide(self, ctx: DecisionContext) -> list[Action]:
            self.contexts.append(ctx)
            return []

    spec = copy.deepcopy(SOLANA_SPEC)
    spec["num_rounds"] = 2
    engine = build_engine(spec)
    execution = engine._execution_model
    execution._leader_schedule = LeaderSchedule(
        [ValidatorStake(pubkey="leader-A", stake_lamports=1)],
        seed=7,
    )
    agent = CapturingAgent()
    engine._agents = [agent]
    engine._agent_rngs[agent.agent_id] = np.random.default_rng(0)

    engine.step()
    engine.step()

    assert [ctx.current_slot for ctx in agent.contexts] == [1, 2]
    assert [ctx.current_leader for ctx in agent.contexts] == ["leader-A", "leader-A"]


def test_world_mode_per_market_routing_still_works_under_slot_pipeline() -> None:
    """World mode + Solana execution routes per-market identically to the legacy
    path. Verified across two markets: per-market trade ordering and aggregate
    fee attribution must match the engine's pre-slot pipeline.

    Stock NoiseTrader emits no actions in world mode (DecisionContext.market_state
    is None), so the test injects a scripted agent that emits MultiMarketActions
    against both markets to actually exercise per-market routing.
    """
    from defi_sim.core.agent import Agent, DecisionContext
    from defi_sim.core.types import (
        Action,
        AgentState,
        MultiMarketAction,
        SwapAction,
    )
    from defi_sim.engine.events import EventType

    world_spec: dict = {
        "market": {
            "type": "world",
            "markets": {
                "amm_a": {
                    "type": "cfamm",
                    "tokens": [
                        {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
                        {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                    ],
                    "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
                },
                "amm_b": {
                    "type": "cfamm",
                    "tokens": [
                        {"id": "BONK", "symbol": "BONK", "decimals": 5, "standard": "spl"},
                        {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
                    ],
                    "params": {"initial_liquidity": 500_000, "collateral_token": "USDC"},
                },
            },
        },
        "agents": [
            {
                "type": "noise",
                "agent_id": "scripted",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000, "BONK": 1_000_000_000},
            },
        ],
        "num_rounds": 4,
        "snapshot_interval": 1,
        "seed": 11,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
            # cost_token defaults to COLLATERAL; pin to USDC so the scripted
            # agent can pay the priority fee and ACTION_EXECUTED fires.
            "params": {"cost_token": "USDC"},
        },
    }

    class WorldScripted(Agent):
        """Emits a SOL/USDC swap on amm_a and a BONK/USDC swap on amm_b each round."""

        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            self.state = AgentState(agent_id=agent_id)

        def decide(self, ctx: DecisionContext) -> list[Action]:
            return [
                MultiMarketAction(
                    agent_id=self.agent_id,
                    market_name="amm_a",
                    inner=SwapAction(
                        agent_id=self.agent_id,
                        token_in="USDC",
                        token_out="SOL",
                        amount_in=1000,
                    ),
                ),
                MultiMarketAction(
                    agent_id=self.agent_id,
                    market_name="amm_b",
                    inner=SwapAction(
                        agent_id=self.agent_id,
                        token_in="USDC",
                        token_out="BONK",
                        amount_in=1000,
                    ),
                ),
            ]

    def _build_with_agent(spec_in: dict) -> SimulationEngine:
        engine = build_engine(copy.deepcopy(spec_in))
        scripted = WorldScripted("scripted")
        scripted.state.balances = dict(engine._agents[0].state.balances)
        engine._agents = [scripted]
        return engine

    def _per_market_executed(engine: SimulationEngine) -> list[tuple[int, str, str]]:
        captured: list[tuple[int, str, str]] = []

        def listener(evt) -> None:
            inner = evt.data.get("action")
            # ACTION_EXECUTED carries the inner SwapAction (post MultiMarketAction
            # unwrap); identify the market by its token_out.
            token_out = getattr(inner, "token_out", None)
            market_label = {"SOL": "amm_a", "BONK": "amm_b"}.get(token_out, "unknown")
            captured.append((evt.round, market_label, inner.__class__.__name__ if inner else ""))

        engine._bus.on(EventType.ACTION_EXECUTED, listener)
        engine.run()
        return captured

    engine_slot = _build_with_agent(world_spec)
    assert isinstance(engine_slot._execution_model, SolanaLikeExecution)

    engine_legacy = _build_with_agent(world_spec)
    engine_legacy._execution_model.supports_slot_execution = lambda: False

    slot_events = _per_market_executed(engine_slot)
    legacy_events = _per_market_executed(engine_legacy)

    # Per-market routing equivalence: ordered per-market trade traces match.
    assert slot_events == legacy_events
    assert slot_events, "expected ACTION_EXECUTED events from scripted multi-market trades"
    # And both markets were actually touched.
    market_names = {m for _, m, _ in slot_events}
    assert market_names == {"amm_a", "amm_b"}

    # Fee attribution equivalence: per-market collected fees match across paths.
    slot_fees = {
        name: mkt.lp_state.accumulated_fees if hasattr(mkt, "lp_state") else None
        for name, mkt in engine_slot._market.markets.items()
    }
    legacy_fees = {
        name: mkt.lp_state.accumulated_fees if hasattr(mkt, "lp_state") else None
        for name, mkt in engine_legacy._market.markets.items()
    }
    assert slot_fees == legacy_fees


def test_eip1559_base_fee_evolution_matches_legacy_under_slot_pipeline() -> None:
    """Regression: on_slot_end must fire ONCE per slot, not once per phase.

    With EIP1559Cost(target=1) and exactly one trading action per round, legacy
    on_round_end(1) leaves base_fee unchanged (1 == target). The slot pipeline
    must produce the same result; if on_slot_end fired twice (once for trading,
    once for empty LP) it would underflow the base fee on the LP phase.
    """
    from defi_sim.engine.execution import SolanaLikeExecution
    from defi_sim.engine.gas import EIP1559Cost
    from defi_sim.engine.ordering import FIFOOrdering

    spec = copy.deepcopy(SOLANA_SPEC)
    # Force exactly one swap per round via a scripted agent.
    engine_slot = build_engine(copy.deepcopy(spec))
    engine_legacy = build_engine(copy.deepcopy(spec))

    # Replace cost models with EIP1559 and force the scripted single-swap path
    # by swapping the agents for a deterministic emitter.
    from defi_sim.core.agent import Agent, DecisionContext
    from defi_sim.core.types import Action, AgentState, SwapAction

    class OneSwap(Agent):
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            self.state = AgentState(agent_id=agent_id)

        def decide(self, ctx: DecisionContext) -> list[Action]:
            return [
                SwapAction(
                    agent_id=self.agent_id,
                    token_in="USDC",
                    token_out="SOL",
                    amount_in=10,
                )
            ]

    for engine in (engine_slot, engine_legacy):
        agent = OneSwap("noise-1")
        agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
        engine._agents = [agent]
        engine._execution_model = SolanaLikeExecution(
            ordering=FIFOOrdering(),
            cost_model=EIP1559Cost(base_fee=100, target_actions_per_round=1),
            cost_token="USDC",
        )

    engine_legacy._execution_model.supports_slot_execution = lambda: False

    engine_slot.run()
    engine_legacy.run()

    slot_base = engine_slot._execution_model._cost_model._base_fee
    legacy_base = engine_legacy._execution_model._cost_model._base_fee
    assert slot_base == legacy_base, (
        f"EIP1559 base fee diverged under slot pipeline: slot={slot_base} legacy={legacy_base}"
    )


def test_admission_runs_once_per_slot_across_trading_and_lp() -> None:
    """Regression: admit() must be called once per slot on the trading+LP union,
    not once per phase. A capacity-limited admission policy that admits exactly
    N actions across the slot must apply slot-wide, not per phase."""
    from defi_sim.core.agent import Agent, DecisionContext
    from defi_sim.core.types import (
        Action,
        AgentState,
        LPAction,
        LPActionType,
        SwapAction,
    )
    from defi_sim.engine.execution import SolanaLikeExecution

    class MixedAgent(Agent):
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            self.state = AgentState(agent_id=agent_id)

        def decide(self, ctx: DecisionContext) -> list[Action]:
            return [
                SwapAction(
                    agent_id=self.agent_id,
                    token_in="USDC",
                    token_out="SOL",
                    amount_in=10,
                ),
                LPAction(
                    agent_id=self.agent_id,
                    lp_type=LPActionType.DEPOSIT,
                    collateral="USDC",
                    amount=100,
                ),
            ]

    spec = copy.deepcopy(SOLANA_SPEC)
    spec["num_rounds"] = 1
    engine = build_engine(spec)
    # Reuse an existing agent_id so engine._agent_rngs lookup succeeds.
    agent = MixedAgent("noise-1")
    agent.state.balances = {"USDC": 1_000_000_000, "SOL": 1_000_000_000}
    engine._agents = [agent]

    admit_calls: list[int] = []

    def slot_wide_cap(actions, round, context):
        admit_calls.append(len(actions))
        # Admit at most one action per slot, drop the rest.
        admitted = list(actions[:1])
        dropped = [(a, "slot capacity") for a in actions[1:]]
        return admitted, dropped

    model = SolanaLikeExecution()
    model._admission_policy = slot_wide_cap
    engine._execution_model = model

    engine.run()

    # admit() must have been called ONCE per round with both trading and LP
    # actions visible (i.e. 2 pending), not twice (1 trading + 1 LP separately).
    assert admit_calls == [2], f"expected single admit() call with 2 actions, got {admit_calls}"
