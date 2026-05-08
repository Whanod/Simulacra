from __future__ import annotations

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.market import Liquidatable, Market, PricedMarket
from defi_sim.core.types import (
    Action,
    AgentState,
    ExecutionContext,
    ExecutionResult,
    LiquidateAction,
    MarketSnapshot,
    MultiMarketAction,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.events import EventBus, EventType
from defi_sim.engine.execution import DirectExecution
from defi_sim.engine.ordering import FIFOOrdering
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.world import World


class StaticMarket(Market):
    market_type = "static"

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["X"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "StaticMarket":
        return StaticMarket()

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "StaticMarket":
        return cls()


class LiquidationMarket(Market, Liquidatable):
    market_type = "liquidation_market"

    def __init__(self) -> None:
        self.liquidations: list[tuple[str, str]] = []

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["DEBT", "COLLATERAL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        if not isinstance(action, LiquidateAction):
            return ExecutionResult(success=False, error="unsupported")
        self.liquidations.append((action.agent_id, action.target_agent_id))
        return ExecutionResult(success=True)

    def copy(self) -> "LiquidationMarket":
        clone = LiquidationMarket()
        clone.liquidations = list(self.liquidations)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "LiquidationMarket":
        return cls()

    def get_liquidatable_agents(self) -> list[str]:
        return ["target"]

    def compute_liquidation_bonus(self, agent_id: str, repay_amount: int) -> int:
        return repay_amount


class SimplePricedMarket(Market, PricedMarket):
    market_type = "simple_priced"

    def __init__(self, prices: dict[str, int]) -> None:
        self._prices = dict(prices)

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=list(self._prices))

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "SimplePricedMarket":
        return SimplePricedMarket(self._prices)

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "SimplePricedMarket":
        return cls({})

    def get_prices(self) -> dict[str, int]:
        return dict(self._prices)

    def get_depth(self, token: str) -> int:
        return 0


class ScriptedAgent(Agent):
    def __init__(self, agent_id: str, actions: list[Action]) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self._actions = list(actions)
        self.calls = 0

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.calls += 1
        return list(self._actions)


class EventRecordingAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self.events: list[EventType | str] = []

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []

    def on_event(self, event) -> None:
        self.events.append(event.type)


def _config(num_rounds: int = 1, **kwargs) -> SimulationConfig:
    return SimulationConfig(
        num_rounds=num_rounds,
        execution_model=DirectExecution(ordering=FIFOOrdering()),
        **kwargs,
    )


def test_world_mode_liquidations_require_multimarket_wrappers():
    world = World()
    world.add_market("m1", LiquidationMarket())
    world.add_market("m2", LiquidationMarket())
    bus = EventBus(record_history=True)
    liquidator = ScriptedAgent(
        "bot",
        [
            LiquidateAction(
                agent_id="bot",
                target_agent_id="target",
                repay_token="DEBT",
                repay_amount=5,
                seize_token="COLLATERAL",
            )
        ],
    )

    SimulationEngine(world, [liquidator], _config(), event_bus=bus).run()

    assert world.get_market("m1").liquidations == []
    assert world.get_market("m2").liquidations == []
    failures = [event for event in bus.history if event.type == EventType.ACTION_FAILED]
    assert len(failures) == 1
    assert "MultiMarketAction wrappers" in failures[0].data["result"].error


def test_world_mode_targeted_liquidations_only_hit_target_market():
    world = World()
    world.add_market("m1", LiquidationMarket())
    world.add_market("m2", LiquidationMarket())
    liquidator = ScriptedAgent(
        "bot",
        [
            MultiMarketAction(
                agent_id="bot",
                market_name="m1",
                inner=LiquidateAction(
                    agent_id="bot",
                    target_agent_id="target",
                    repay_token="DEBT",
                    repay_amount=5,
                    seize_token="COLLATERAL",
                ),
            )
        ],
    )

    SimulationEngine(world, [liquidator], _config()).run()

    assert world.get_market("m1").liquidations == [("bot", "target")]
    assert world.get_market("m2").liquidations == []


def test_world_mode_records_price_history_for_priced_markets():
    world = World()
    world.add_market("amm", SimplePricedMarket({"A": 10}))
    world.add_market("clob", SimplePricedMarket({"B": 20}))

    result = SimulationEngine(world, [ScriptedAgent("observer", [])], _config(num_rounds=2)).run()

    assert result.price_history == [
        {"amm:A": 10, "clob:B": 20},
        {"amm:A": 10, "clob:B": 20},
    ]


def test_liquidation_phase_does_not_call_decide_twice():
    market = LiquidationMarket()
    liquidator = ScriptedAgent(
        "bot",
        [
            LiquidateAction(
                agent_id="bot",
                target_agent_id="target",
                repay_token="DEBT",
                repay_amount=5,
                seize_token="COLLATERAL",
            )
        ],
    )

    SimulationEngine(market, [liquidator], _config()).run()

    assert liquidator.calls == 1
    assert market.liquidations == [("bot", "target")]


def test_snapshot_callback_receives_a_defensive_copy():
    seen = []

    def mutate(snap) -> None:
        snap.agent_states["alice"].balances["MUTATED"] = 1
        seen.append(snap)

    result = SimulationEngine(
        StaticMarket(),
        [ScriptedAgent("alice", [])],
        _config(snapshot_interval=1, snapshot_callback=mutate, retain_snapshots=True),
    ).run()

    assert seen[0].agent_states["alice"].balances == {"MUTATED": 1}
    assert result.round_snapshots[0].agent_states["alice"].balances == {}


def test_agents_receive_events_via_on_event():
    bus = EventBus(record_history=True)
    agent = EventRecordingAgent("alice")

    SimulationEngine(StaticMarket(), [agent], _config(), event_bus=bus).run()

    assert agent.events == [event.type for event in bus.history]
    assert EventType.ROUND_START in agent.events
    assert EventType.ROUND_END in agent.events
