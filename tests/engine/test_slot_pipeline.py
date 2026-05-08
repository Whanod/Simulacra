"""Unit tests for the slot-coordinated execution seam (PRD Phase 1.0)."""

from __future__ import annotations

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.market import Liquidatable, Market
from defi_sim.core.types import (
    Action,
    AgentState,
    ExecutionContext,
    ExecutionResult,
    LiquidateAction,
    LPAction,
    LPActionType,
    MarketSnapshot,
    SwapAction,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.events import EventBus, EventType
from defi_sim.engine.execution import (
    BatchExecution,
    DirectExecution,
    SolanaLikeExecution,
    deserialize_execution_model,
    serialize_execution_model,
)
from defi_sim.engine.gas import ZeroCost
from defi_sim.engine.ordering import FIFOOrdering, OrderingContext
from defi_sim.engine.scheduler import LockedAction, PriorityScheduler, SerialScheduler
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.slot import ExecutedAction, SlotContext


def _make_actions(n: int) -> list[SwapAction]:
    return [SwapAction(agent_id=f"a{i}") for i in range(n)]


def _slot_context(actions, executor, *, slot=1, run_liquidations=None) -> SlotContext:
    return SlotContext(
        slot=slot,
        pending_actions=list(actions),
        ordering_context=OrderingContext(),
        executor=executor,
        emit=lambda evt: None,
        run_liquidations=run_liquidations or (lambda: None),
    )


def _passthrough_executor():
    calls: list[SwapAction] = []

    def execute(action, slot):
        calls.append(action)
        return ExecutedAction(
            action=action,
            execution_cost=0,
            cost_token=None,
            succeeded=True,
            failure_reason=None,
        )

    return execute, calls


def test_supports_slot_execution_default_false_on_direct_and_batch() -> None:
    assert DirectExecution().supports_slot_execution() is False
    assert BatchExecution().supports_slot_execution() is False


def test_supports_slot_execution_true_on_solana_like() -> None:
    assert SolanaLikeExecution().supports_slot_execution() is True


def test_execute_slot_with_serial_scheduler_returns_actions_in_input_order() -> None:
    model = SolanaLikeExecution(scheduler=SerialScheduler())
    actions = _make_actions(5)
    executor, calls = _passthrough_executor()
    ctx = _slot_context(actions, executor)
    outcome = model.execute_slot(ctx)

    assert [ea.action for ea in outcome.executed] == actions
    assert calls == actions
    assert outcome.dropped == []
    assert outcome.deferred == []


def test_execute_slot_admit_drops_surface_in_outcome() -> None:
    actions = _make_actions(5)

    def admission_policy(actions_in, round, context):
        admitted = list(actions_in[:3])
        dropped = [(a, "policy reject") for a in actions_in[3:]]
        return admitted, dropped

    model = SolanaLikeExecution()
    # Inject an admission policy via the BatchExecution-private hook used at runtime.
    model._admission_policy = admission_policy

    executor, calls = _passthrough_executor()
    ctx = _slot_context(actions, executor)
    outcome = model.execute_slot(ctx)

    assert len(outcome.dropped) == 2
    assert all(reason == "policy reject" for _, reason in outcome.dropped)
    assert len(outcome.executed) == 3
    assert calls == actions[:3]


def test_execute_slot_with_no_scheduler_argument_uses_priority_default() -> None:
    """PRD US-003 step 4: ``SolanaLikeExecution`` now defaults to
    ``PriorityScheduler``. Test fixtures that bypass the engine receive
    empty-lock ``LockedAction`` wrapping, which yields N single-action
    lanes — every action still executes once."""
    model = SolanaLikeExecution()
    actions = _make_actions(3)
    executor, calls = _passthrough_executor()
    outcome = model.execute_slot(_slot_context(actions, executor))

    assert isinstance(model._scheduler, PriorityScheduler)
    assert sorted(calls, key=lambda a: a.agent_id) == actions
    assert len(outcome.executed) == 3


def test_slot_outcome_deferred_is_empty_in_1_0() -> None:
    model = SolanaLikeExecution()
    actions = _make_actions(4)
    executor, _ = _passthrough_executor()
    outcome = model.execute_slot(_slot_context(actions, executor))
    assert outcome.deferred == []


def test_solana_like_default_scheduler_round_trips() -> None:
    """PRD US-003 step 4: default scheduler is ``PriorityScheduler`` and
    its discriminator round-trips through serialize/deserialize."""
    model = SolanaLikeExecution()
    data = serialize_execution_model(model)
    assert data["scheduler"] == {"type": "priority"}
    restored = deserialize_execution_model(data)
    assert isinstance(restored, SolanaLikeExecution)
    assert isinstance(restored._scheduler, PriorityScheduler)


def test_solana_like_with_explicit_serial_scheduler_round_trips() -> None:
    """Explicit ``SerialScheduler`` opt-in still round-trips."""
    model = SolanaLikeExecution(scheduler=SerialScheduler())
    data = serialize_execution_model(model)
    assert data["scheduler"] == {"type": "serial"}
    restored = deserialize_execution_model(data)
    assert isinstance(restored, SolanaLikeExecution)
    assert isinstance(restored._scheduler, SerialScheduler)


def test_legacy_solana_snapshot_without_scheduler_defaults_to_serial() -> None:
    """Snapshots predating the ``scheduler`` field still load and pin to
    ``SerialScheduler`` for backwards compatibility (legacy shape was
    serial-only)."""
    model = SolanaLikeExecution(scheduler=SerialScheduler())
    data = serialize_execution_model(model)
    data.pop("scheduler", None)
    restored = deserialize_execution_model(data)
    assert isinstance(restored, SolanaLikeExecution)
    assert isinstance(restored._scheduler, SerialScheduler)


# ---------------------------------------------------------------------------
# Phase-ordering regression test (PRD Implementation step "Phase-bucket
# preservation"): the slot pipeline must keep TRADING -> LIQUIDATION -> LP
# ordering for any spec mixing swap, liquidate, and LP actions.
# ---------------------------------------------------------------------------


class _PhaseRecordingMarket(Market, Liquidatable):
    """Test market that records execute() calls in arrival order and supports
    swap, LP, and liquidation action shapes."""

    market_type = "phase_recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["USDC", "SOL", "DEBT", "COLLATERAL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        kind = type(action).__name__
        self.calls.append((kind, action.agent_id))
        return ExecutionResult(success=True)

    def copy(self) -> "_PhaseRecordingMarket":
        clone = _PhaseRecordingMarket()
        clone.calls = list(self.calls)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "_PhaseRecordingMarket":
        return cls()

    def get_liquidatable_agents(self) -> list[str]:
        return ["debtor"]

    def compute_liquidation_bonus(self, agent_id: str, repay_amount: int) -> int:
        return repay_amount

    def resolve_locks(self, action: Action, state=None) -> LockedAction:
        return LockedAction(action=action)


class _ScriptedAgent(Agent):
    def __init__(self, agent_id: str, actions: list[Action]) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._actions = list(actions)

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return list(self._actions)


def _phase_trace(execution_model) -> list[tuple[str, str]]:
    """Run a fixed swap/liquidate/LP scenario and return ordered execute() calls."""
    market = _PhaseRecordingMarket()
    swapper = _ScriptedAgent(
        "swapper",
        [SwapAction(agent_id="swapper", token_in="USDC", token_out="SOL", amount_in=10)],
    )
    lp = _ScriptedAgent(
        "lp",
        [LPAction(agent_id="lp", lp_type=LPActionType.DEPOSIT, collateral="USDC", amount=100)],
    )
    liquidator = _ScriptedAgent(
        "liquidator",
        [
            LiquidateAction(
                agent_id="liquidator",
                target_agent_id="debtor",
                repay_token="DEBT",
                repay_amount=5,
                seize_token="COLLATERAL",
            )
        ],
    )
    config = SimulationConfig(num_rounds=1, execution_model=execution_model)
    SimulationEngine(market, [swapper, lp, liquidator], config).run()
    return market.calls


def test_solana_execution_preserves_trading_then_liquidation_then_lp_order() -> None:
    """SolanaLikeExecution+SerialScheduler must produce identical phase ordering
    to plain BatchExecution: TRADING (swap) -> LIQUIDATION -> LP."""
    batch_trace = _phase_trace(BatchExecution(ordering=FIFOOrdering()))
    solana_trace = _phase_trace(
        SolanaLikeExecution(ordering=FIFOOrdering(), cost_model=ZeroCost(), scheduler=SerialScheduler())
    )

    assert batch_trace == solana_trace
    # And the absolute ordering matches the documented phase order.
    assert [kind for kind, _ in solana_trace] == [
        "SwapAction",
        "LiquidateAction",
        "LPAction",
    ]


def test_solana_execution_preserves_action_event_order() -> None:
    """ACTION_EXECUTED events for swap/liquidate/LP must surface in phase order
    under the slot pipeline, identically to the legacy path."""

    def _event_kinds(model) -> list[str]:
        market = _PhaseRecordingMarket()
        bus = EventBus(record_history=True)
        agents = [
            _ScriptedAgent(
                "swapper",
                [SwapAction(agent_id="swapper", token_in="USDC", token_out="SOL", amount_in=10)],
            ),
            _ScriptedAgent(
                "lp",
                [LPAction(agent_id="lp", lp_type=LPActionType.DEPOSIT, collateral="USDC", amount=100)],
            ),
            _ScriptedAgent(
                "liquidator",
                [
                    LiquidateAction(
                        agent_id="liquidator",
                        target_agent_id="debtor",
                        repay_token="DEBT",
                        repay_amount=5,
                        seize_token="COLLATERAL",
                    )
                ],
            ),
        ]
        config = SimulationConfig(num_rounds=1, execution_model=model)
        SimulationEngine(market, agents, config, event_bus=bus).run()
        return [
            evt.data.get("action").__class__.__name__
            for evt in bus.history
            if evt.type == EventType.ACTION_EXECUTED and evt.data.get("action") is not None
        ]

    assert _event_kinds(BatchExecution(ordering=FIFOOrdering())) == _event_kinds(
        SolanaLikeExecution(ordering=FIFOOrdering(), cost_model=ZeroCost(), scheduler=SerialScheduler())
    )
