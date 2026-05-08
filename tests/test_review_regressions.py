from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from defi_sim.agents.population import PopulationBuilder, PopulationConfig
from defi_sim.agents.noise import NoiseParams, NoiseTrader
from defi_sim.core.agent import Agent, DecisionContext, DelayedInformation, FullTransparency, InformationFilter
from defi_sim.core.clock import BlockClock
from defi_sim.core.market import Liquidatable, Market
from defi_sim.core.types import (
    Action,
    AgentState,
    AgentRole,
    AmmSnapshot,
    AtomicAction,
    BundleAction,
    ClobSnapshot,
    ExecutionContext,
    ExecutionResult,
    FLOAT_MODE,
    FlashLoanAction,
    LPAction,
    LPActionType,
    LiquidateAction,
    MarketSnapshot,
    MultiMarketAction,
    OrderAction,
    OrderSide,
    Side,
    SingleAssetAction,
    ThresholdPredicate,
    SwapAction,
    Token,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.execution import BatchExecution, DirectExecution
from defi_sim.engine.events import EventBus, EventType
from defi_sim.engine.feeds import HistoricalFeed, StochasticFeed
from defi_sim.engine.gas import EIP1559Cost, FixedGas
from defi_sim.engine.ordering import FIFOOrdering, RandomOrdering
from defi_sim.engine.parameters import ParameterStore, ScheduledChange
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.snapshots import restore, snapshot
from defi_sim.engine.sweeps import SweepConfig, run_sweep
from defi_sim.engine.world import World
from defi_sim.fees.models import dynamic_fee, flat_fee, spread_fee
from defi_sim.incentives.emissions import DecayingEmission, FixedRateEmission
from defi_sim.markets.cfamm import CfammMarket
from defi_sim.markets.clob import ClobMarket
from defi_sim.metrics.generic import compute_slippage
from defi_sim.metrics.registry import MetricRegistry
from defi_sim.orderbook.orderbook import OBSide, Order
from defi_sim.validation.checks import check_conservation


class ScriptedAgent(Agent):
    def __init__(self, agent_id: str, actions_by_round: dict[int, list[Action]], balances: dict[str, int] | None = None):
        self.agent_id = agent_id
        self.actions_by_round = actions_by_round
        self.state = AgentState(agent_id=agent_id, balances=dict(balances or {}))

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return list(self.actions_by_round.get(ctx.current_round, []))


class NoopAgent(Agent):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []


class RecordingMarket(Market):
    market_type = "recording"

    def __init__(self):
        self.executed: list[str] = []

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["ASSET"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.executed.append(type(action).__name__)
        return ExecutionResult(success=True)

    def copy(self) -> "RecordingMarket":
        clone = RecordingMarket()
        clone.executed = list(self.executed)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "RecordingMarket":
        return cls()


class RoundRecordingMarket(Market):
    market_type = "round_recording"

    def __init__(self):
        self.rounds: list[int] = []

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["ASSET"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.rounds.append(ctx.current_round)
        return ExecutionResult(success=True)

    def copy(self) -> "RoundRecordingMarket":
        clone = RoundRecordingMarket()
        clone.rounds = list(self.rounds)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "RoundRecordingMarket":
        return cls()


class FixedStateMarket(Market):
    market_type = "fixed_state"

    def __init__(self, token: str):
        self.token = token

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(num_assets=1, tokens=[self.token])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=False, error="unsupported")

    def copy(self) -> "FixedStateMarket":
        return FixedStateMarket(self.token)

    def to_bytes(self) -> bytes:
        return self.token.encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "FixedStateMarket":
        return cls(data.decode())


class FailingMarket(Market):
    market_type = "failing"

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["ASSET"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=False, error="boom")

    def copy(self) -> "FailingMarket":
        return FailingMarket()

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "FailingMarket":
        return cls()


class GasCreditMarket(Market):
    market_type = "gas_credit"

    def __init__(self):
        self.executed = 0

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["COLLATERAL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.executed += 1
        return ExecutionResult(success=True, token_deltas={"COLLATERAL": 10})

    def copy(self) -> "GasCreditMarket":
        clone = GasCreditMarket()
        clone.executed = self.executed
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "GasCreditMarket":
        return cls()


class LiquidationRecordingMarket(Market, Liquidatable):
    market_type = "liquidation_recording"

    def __init__(self):
        self.liquidations: list[tuple[str, str]] = []

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["DEBT", "COLLATERAL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        if not isinstance(action, LiquidateAction):
            return ExecutionResult(success=False, error="unsupported")
        self.liquidations.append((action.agent_id, action.target_agent_id))
        return ExecutionResult(success=True, token_deltas={action.seize_token: action.repay_amount})

    def copy(self) -> "LiquidationRecordingMarket":
        clone = LiquidationRecordingMarket()
        clone.liquidations = list(self.liquidations)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "LiquidationRecordingMarket":
        return cls()

    def get_liquidatable_agents(self) -> list[str]:
        return ["target"]

    def compute_liquidation_bonus(self, agent_id: str, repay_amount: int) -> int:
        return repay_amount


class RecordingWorldAgent(Agent):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id, role=AgentRole("observer"))
        self.seen_worlds: list[dict[str, list[str]]] = []

    def decide(self, ctx: DecisionContext) -> list[Action]:
        all_markets = getattr(ctx, "all_markets", {})
        self.seen_worlds.append({name: list(state.tokens) for name, state in all_markets.items()})
        return []


class RedactingWorldFilter(InformationFilter):
    def filter_market_state(self, agent: Agent, state: MarketSnapshot) -> MarketSnapshot:
        return MarketSnapshot(num_assets=state.num_assets, tokens=["single"])

    def filter_feed_prices(self, agent: Agent, prices: dict[str, int]) -> dict[str, int] | None:
        return prices

    def filter_all_market_states(self, agent: Agent, states: dict[str, MarketSnapshot]) -> dict[str, MarketSnapshot]:
        return {
            name: MarketSnapshot(num_assets=state.num_assets, tokens=[f"filtered:{name}"])
            for name, state in states.items()
        }


class PendingAwareAgent(Agent):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self.pending_by_round: dict[int, list[Action] | None] = {}

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.pending_by_round[ctx.current_round] = ctx.pending_actions
        return []


class ExplodingAgent(Agent):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)

    def decide(self, ctx: DecisionContext) -> list[Action]:
        raise RuntimeError("boom")


class CountingLiquidatorAgent(Agent):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.calls = 0
        self.state = AgentState(agent_id=agent_id, role=AgentRole("searcher"))

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.calls += 1
        return [
            LiquidateAction(
                agent_id=self.agent_id,
                target_agent_id="target",
                repay_token="DEBT",
                repay_amount=5,
                seize_token="COLLATERAL",
            )
        ]


@dataclass
class ReserveSnapshot(MarketSnapshot):
    reserves: dict[str, int] = field(default_factory=dict)


def test_clob_resting_orders_lock_balances_and_credit_makers_on_fill():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    quote = Token(id="USDC", symbol="USDC", decimals=0)
    market = ClobMarket(pairs=[(base, quote)])

    maker = ScriptedAgent(
        "maker",
        {
            1: [OrderAction(agent_id="maker", base="ETH", quote="USDC", side=OrderSide.SELL, price=10, quantity=5)],
        },
        balances={"ETH": 5},
    )
    taker = ScriptedAgent(
        "taker",
        {
            2: [SingleAssetAction(agent_id="taker", asset="ETH", collateral="USDC", amount=50, side=Side.BUY)],
        },
        balances={"USDC": 100},
    )

    engine = SimulationEngine(
        market=market,
        agents=[maker, taker],
        config=SimulationConfig(num_rounds=2, execution_model=DirectExecution(ordering=FIFOOrdering())),
    )
    engine.run()

    assert maker.state.balance("ETH") == 0
    assert maker.state.balance("USDC") == 50
    assert taker.state.balance("ETH") == 5
    assert taker.state.balance("USDC") == 50


def test_world_mode_defers_lp_actions_and_rejects_plain_actions():
    market = RecordingMarket()
    world = World()
    world.add_market("amm", market)

    agent = ScriptedAgent(
        "alice",
        {
            1: [
                SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=10, side=Side.BUY),
                MultiMarketAction(
                    agent_id="alice",
                    market_name="amm",
                    inner=LPAction(agent_id="alice", collateral="COLLATERAL", amount=10, lp_type=LPActionType.DEPOSIT),
                ),
                MultiMarketAction(
                    agent_id="alice",
                    market_name="amm",
                    inner=SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=5, side=Side.BUY),
                ),
            ]
        },
        balances={"COLLATERAL": 1_000},
    )

    from defi_sim.engine.events import EventBus

    bus = EventBus(record_history=True)
    engine = SimulationEngine(
        market=world,
        agents=[agent],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )

    engine.run()

    assert market.executed == ["SingleAssetAction", "LPAction"]
    failures = [event for event in bus.history if event.type == EventType.ACTION_FAILED]
    assert len(failures) == 1
    assert "MultiMarketAction" in failures[0].data["result"].error


def test_clob_market_buy_does_not_leave_resting_bid():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    quote = Token(id="USDC", symbol="USDC", decimals=0)
    market = ClobMarket(pairs=[(base, quote)])

    maker_ctx = ExecutionContext(agent_state=AgentState(agent_id="maker", balances={"ETH": 5}))
    place = market.execute(
        OrderAction(agent_id="maker", base="ETH", quote="USDC", side=OrderSide.SELL, price=10, quantity=5),
        maker_ctx,
    )
    assert place.success

    taker_ctx = ExecutionContext(agent_state=AgentState(agent_id="taker", balances={"USDC": 100}))
    result = market.execute(
        SingleAssetAction(agent_id="taker", asset="ETH", collateral="USDC", amount=100, side=Side.BUY),
        taker_ctx,
    )

    assert result.success
    assert result.token_deltas == {"ETH": 5, "USDC": -50}

    state = market.get_state()
    assert state.best_bid["ETH"] is None
    assert state.best_ask["ETH"] is None


def test_run_continues_after_step_without_replaying_rounds():
    market = RoundRecordingMarket()
    agent = ScriptedAgent(
        "alice",
        {
            1: [Action(agent_id="alice")],
            2: [Action(agent_id="alice")],
            3: [Action(agent_id="alice")],
        },
    )
    engine = SimulationEngine(
        market=market,
        agents=[agent],
        config=SimulationConfig(num_rounds=3, execution_model=DirectExecution(ordering=FIFOOrdering())),
    )

    first = engine.step()
    result = engine.run()

    assert first.round == 1
    assert market.rounds == [1, 2, 3]
    assert result.num_rounds_executed == 3


def test_snapshot_restore_preserves_world_orderbook_params_and_rng():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    quote = Token(id="USDC", symbol="USDC", decimals=0)
    clob = ClobMarket(pairs=[(base, quote)])

    maker_ctx = ExecutionContext(agent_state=AgentState(agent_id="maker", balances={"ETH": 3}))
    clob.execute(
        OrderAction(agent_id="maker", base="ETH", quote="USDC", side=OrderSide.SELL, price=11, quantity=3),
        maker_ctx,
    )

    world = World()
    world.add_market("clob", clob)

    params = ParameterStore(defaults={"fee_bps": 30})
    params.schedule(ScheduledChange(key="fee_bps", value=45, execute_at_round=2))

    engine = SimulationEngine(
        market=world,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(
            num_rounds=3,
            parameters=params,
            execution_model=DirectExecution(ordering=FIFOOrdering()),
        ),
    )
    engine._current_round = 1
    engine._fee_destination_balances = {"protocol": {"USDC": 7}}
    engine._last_feed_prices = {"ETH": 123}

    data = snapshot(engine)
    expected_next_rng = int(engine._agent_rng.integers(0, 1_000_000))

    engine._current_round = 99
    engine._fee_destination_balances = {}
    engine._last_feed_prices = None
    engine._parameters.set("fee_bps", 99, round=99)
    restore(engine, data)

    restored_market = engine._market.get_market("clob")
    restored_state = restored_market.get_state()
    assert engine._is_world is True
    assert engine.current_round == 1
    assert restored_state.best_ask["ETH"] == 11
    assert restored_state.total_depth["ETH"] == 3
    assert engine._parameters.to_dict()["params"]["fee_bps"] == 30
    assert engine._parameters.to_dict()["pending"][0]["execute_at_round"] == 2
    assert engine._fee_destination_balances == {"protocol": {"USDC": 7}}
    assert engine._last_feed_prices == {"ETH": 123}
    assert int(engine._agent_rng.integers(0, 1_000_000)) == expected_next_rng


def test_snapshot_restore_preserves_execution_model_and_information_filter_state():
    info_filter = DelayedInformation({"observer": 1})
    info_filter.record(MarketSnapshot(num_assets=1, tokens=["ETH"]), {"ETH": 111})

    execution_model = BatchExecution(
        cost_model=EIP1559Cost(base_fee=7),
        ordering=RandomOrdering(),
    )
    engine = SimulationEngine(
        market=FixedStateMarket("ETH"),
        agents=[NoopAgent("observer")],
        config=SimulationConfig(
            num_rounds=1,
            clock=BlockClock(block_time=2),
            information_filter=info_filter,
            execution_model=execution_model,
        ),
    )

    data = snapshot(engine)

    engine._execution_model = DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(99))
    engine._info_filter = FullTransparency()
    restore(engine, data)

    assert isinstance(engine._execution_model, BatchExecution)
    assert engine._execution_model._cost_model._base_fee == 7
    assert isinstance(engine._info_filter, DelayedInformation)
    assert engine._info_filter._delays == {"observer": 1}
    assert engine._info_filter._price_history == [{"ETH": 111}]


def test_fee_splits_route_protocol_fees_and_only_lp_share_goes_to_lps():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000)

    lp = ScriptedAgent(
        "lp",
        {
            1: [LPAction(agent_id="lp", collateral="COLLATERAL", amount=1_000, lp_type=LPActionType.DEPOSIT)],
        },
        balances={"COLLATERAL": 10_000},
    )
    trader = ScriptedAgent(
        "trader",
        {
            2: [SingleAssetAction(agent_id="trader", asset="YES", collateral="COLLATERAL", amount=1_000, side=Side.BUY)],
        },
        balances={"COLLATERAL": 10_000},
    )

    config = SimulationConfig(
        num_rounds=2,
        execution_model=DirectExecution(ordering=FIFOOrdering()),
        default_fee_model=lambda gross, ctx: flat_fee(
            gross,
            ctx,
            trade_fee_bps=100,
            split_config={"lp": 6000, "protocol": 4000},
        ),
    )
    engine = SimulationEngine(market=market, agents=[lp, trader], config=config)
    result = engine.run()

    assert lp.state.balance("COLLATERAL") == 9_006
    assert result.metadata["fee_destination_balances"] == {"protocol": {"COLLATERAL": 4}}

    # Per-round fee_history mirrors fee_splits: round 1 is LP-only deposit
    # (no trade, no fees), round 2 is the trader's buy which pays 10
    # COLLATERAL total split 60/40 between lp and protocol. Splits are
    # keyed by token so mixed-token runs don't collapse into one scalar.
    assert len(result.fee_history) == 2
    assert result.fee_history[0] == {}
    assert result.fee_history[1] == {"lp": {"COLLATERAL": 6}, "protocol": {"COLLATERAL": 4}}


def test_execution_model_cost_configuration_is_applied():
    agent = ScriptedAgent(
        "alice",
        {1: [SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=1, side=Side.BUY)]},
        balances={"COLLATERAL": 100},
    )

    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(3)),
        ),
    )
    engine.run()

    assert agent.state.balance("COLLATERAL") == 97


def test_failed_actions_keep_execution_cost_by_default():
    bus = EventBus(record_history=True)
    agent = ScriptedAgent(
        "alice",
        {1: [SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=1, side=Side.BUY)]},
        balances={"COLLATERAL": 100},
    )

    engine = SimulationEngine(
        market=FailingMarket(),
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(7)),
        ),
        event_bus=bus,
    )
    engine.run()

    [failure] = [event for event in bus.history if event.type == EventType.ACTION_FAILED]
    assert agent.state.balance("COLLATERAL") == 93
    assert failure.data["execution_cost"] == 7
    assert failure.data["gas_cost"] == 7


def test_insufficient_execution_balance_drops_action_before_market_execution():
    bus = EventBus(record_history=True)
    agent = ScriptedAgent(
        "alice",
        {1: [SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=1, side=Side.BUY)]},
        balances={"COLLATERAL": 0},
    )
    market = RecordingMarket()

    engine = SimulationEngine(
        market=market,
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(5)),
        ),
        event_bus=bus,
    )
    engine.run()

    dropped = [event for event in bus.history if event.type == EventType.ACTION_DROPPED]
    assert len(dropped) == 1
    assert market.executed == []


def test_execution_costs_are_reserved_before_protocol_execution_changes_balances():
    bus = EventBus(record_history=True)
    agent = ScriptedAgent(
        "alice",
        {1: [Action(agent_id="alice"), Action(agent_id="alice")]},
        balances={"COLLATERAL": 10},
    )
    market = GasCreditMarket()

    engine = SimulationEngine(
        market=market,
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(7)),
        ),
        event_bus=bus,
    )
    engine.run()

    dropped = [event for event in bus.history if event.type == EventType.ACTION_DROPPED]
    executed = [event for event in bus.history if event.type == EventType.ACTION_EXECUTED]

    assert len(dropped) == 1
    assert len(executed) == 1
    assert market.executed == 1
    assert agent.state.balance("COLLATERAL") == 13


def test_execution_model_controls_pending_queue_visibility():
    first = ScriptedAgent(
        "maker",
        {1: [SingleAssetAction(agent_id="maker", asset="ASSET", collateral="COLLATERAL", amount=1, side=Side.BUY)]},
        balances={"COLLATERAL": 10},
    )
    observer = PendingAwareAgent("observer")

    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[first, observer],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), expose_pending_actions=True),
        ),
    )
    engine.run()

    [pending] = observer.pending_by_round.values()
    assert pending is not None
    assert len(pending) == 1
    assert pending[0].agent_id == "maker"


def test_agent_decision_exceptions_are_not_silently_swallowed():
    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[ExplodingAgent("alice")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
    )

    with pytest.raises(RuntimeError, match="boom"):
        engine.run()


def test_atomic_action_pays_gas_once():
    agent = ScriptedAgent(
        "alice",
        {
            1: [
                AtomicAction(
                    agent_id="alice",
                    actions=[SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=1)],
                )
            ]
        },
        balances={"COLLATERAL": 100},
    )

    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(7)),
        ),
    )
    engine.run()

    assert agent.state.balance("COLLATERAL") == 93


def test_atomic_action_preserves_market_state_for_stateful_fees():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]

    def run(actions: list[Action]) -> dict[str, dict[str, int]]:
        market = CfammMarket(tokens=tokens, initial_liquidity=10_000, fee_model=dynamic_fee)
        warm_ctx = ExecutionContext(
            agent_state=AgentState(agent_id="warm", balances={"COLLATERAL": 10_000}),
        )
        market.execute(
            SingleAssetAction(
                agent_id="warm",
                asset="YES",
                collateral="COLLATERAL",
                amount=5_000,
                side=Side.BUY,
            ),
            warm_ctx,
        )

        agent = ScriptedAgent("alice", {1: actions}, balances={"COLLATERAL": 10_000})
        engine = SimulationEngine(
            market=market,
            agents=[agent],
            config=SimulationConfig(
                num_rounds=1,
                execution_model=DirectExecution(ordering=FIFOOrdering()),
            ),
        )
        result = engine.run()
        return result.metadata["fee_destination_balances"]

    direct = run([
        SingleAssetAction(
            agent_id="alice",
            asset="YES",
            collateral="COLLATERAL",
            amount=1_000,
            side=Side.BUY,
        ),
    ])
    atomic = run([
        AtomicAction(
            agent_id="alice",
            actions=[
                SingleAssetAction(
                    agent_id="alice",
                    asset="YES",
                    collateral="COLLATERAL",
                    amount=1_000,
                    side=Side.BUY,
                ),
            ],
        ),
    ])

    assert atomic == direct


def test_flash_loan_action_pays_gas_once():
    agent = ScriptedAgent(
        "alice",
        {
            1: [
                FlashLoanAction(
                    agent_id="alice",
                    token="FLASH",
                    amount=10,
                    inner_actions=[SingleAssetAction(agent_id="alice", asset="ASSET", collateral="COLLATERAL", amount=1)],
                )
            ]
        },
        balances={"COLLATERAL": 100},
    )

    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_model=FixedGas(9)),
        ),
    )
    engine.run()

    assert agent.state.balance("COLLATERAL") == 91


def test_snapshot_restore_preserves_importable_market_fee_model():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000, fee_model=flat_fee)
    engine = SimulationEngine(
        market=market,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
    )

    data = snapshot(engine)
    restore(engine, data)

    assert engine._market.fee_model is flat_fee


def test_snapshot_restore_reattaches_world_event_bus():
    bus = EventBus(record_history=True)
    base = Token(id="ETH", symbol="ETH", decimals=0)
    quote = Token(id="USDC", symbol="USDC", decimals=0)

    world = World()
    engine = SimulationEngine(
        market=world,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )
    world.add_market("one", ClobMarket(pairs=[(base, quote)]))

    data = snapshot(engine)
    restore(engine, data)
    engine._market.add_market("two", ClobMarket(pairs=[(base, quote)]))

    assert [event.type for event in bus.history] == [
        EventType.MARKET_ADDED,
        EventType.MARKET_ADDED,
    ]


def test_cfamm_swap_action_supports_collateral_and_asset_hops():
    tokens = [
        Token(id="ETH", symbol="ETH", decimals=0),
        Token(id="DAI", symbol="DAI", decimals=0),
    ]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000)

    buy_ctx = ExecutionContext(agent_state=AgentState(agent_id="trader", balances={"USDC": 1_000}))
    buy_result = market.execute(
        SwapAction(agent_id="trader", token_in="USDC", token_out="ETH", amount_in=1_000),
        buy_ctx,
    )

    assert buy_result.success
    assert buy_result.token_deltas["USDC"] == -1_000
    assert buy_result.token_deltas["ETH"] > 0

    hop_ctx = ExecutionContext(agent_state=AgentState(agent_id="trader", balances={"ETH": 500}))
    hop_result = market.execute(
        SwapAction(agent_id="trader", token_in="ETH", token_out="DAI", amount_in=100),
        hop_ctx,
    )

    assert hop_result.success
    assert hop_result.token_deltas["ETH"] == -100
    assert hop_result.token_deltas["DAI"] > 0
    assert "COLLATERAL" not in hop_result.token_deltas


def test_cfamm_float_bundle_buy_preserves_fractional_weights():
    tokens = [
        Token(id="A", symbol="A", decimals=0),
        Token(id="B", symbol="B", decimals=0),
    ]
    market = CfammMarket(tokens=tokens, initial_liquidity=1_000.0)
    ctx = ExecutionContext(agent_state=AgentState(agent_id="buyer", balances={"USD": 100.0}))

    result = market.execute(
        BundleAction(
            agent_id="buyer",
            collateral="USD",
            amount=100.0,
            weights={"A": 0.2, "B": 0.8},
            side=Side.BUY,
        ),
        ctx,
    )

    assert result.success
    assert result.token_deltas["A"] > 0
    assert result.token_deltas["B"] > result.token_deltas["A"]


def test_cfamm_respects_engine_numeric_mode_even_with_integer_liquidity():
    tokens = [
        Token(id="A", symbol="A", decimals=0),
        Token(id="B", symbol="B", decimals=0),
    ]
    market = CfammMarket(tokens=tokens, initial_liquidity=1_000)
    engine = SimulationEngine(
        market=market,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, numeric_mode=FLOAT_MODE),
    )

    engine.run()

    prices = market.get_prices()
    assert all(isinstance(price, float) for price in prices.values())
    assert all(isinstance(reserve, float) for reserve in market.get_state().reserves.values())


def test_validation_hook_runs_custom_checks():
    from defi_sim.engine.events import EventBus
    from defi_sim.validation.checks import ValidationHook

    def custom_check(market):
        return False

    bus = EventBus(record_history=True)
    hook = ValidationHook(
        bus,
        checks=[custom_check],
        fail_fast=False,
        market=RecordingMarket(),
    )
    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )

    engine.run()

    assert hook.violations
    assert "custom_check failed" in hook.violations[0][1]


def test_check_conservation_accepts_generic_snapshots_with_reserves():
    pre = ReserveSnapshot(tokens=["A"], reserves={"A": 10})
    post = ReserveSnapshot(tokens=["A"], reserves={"A": 11})
    bad_post = ReserveSnapshot(tokens=["A"], reserves={"A": 12})
    result = ExecutionResult(success=True, token_deltas={"A": -1})

    assert check_conservation(pre, post, result) is True
    assert check_conservation(pre, bad_post, result) is False


def test_cfamm_lp_rebalance_is_rejected_for_uniform_pool():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000)
    ctx = ExecutionContext(agent_state=AgentState(agent_id="lp", balances={"COLLATERAL": 5_000}))

    deposit = market.execute(
        LPAction(agent_id="lp", collateral="COLLATERAL", amount=1_000, lp_type=LPActionType.DEPOSIT),
        ctx,
    )
    assert deposit.success

    pre = market.get_state()
    result = market.execute(
        LPAction(
            agent_id="lp",
            collateral="COLLATERAL",
            amount=0,
            lp_type=LPActionType.REBALANCE,
            target_weights={"YES": 900, "NO": 100},
        ),
        ctx,
    )
    post = market.get_state()

    assert result.success is False
    assert "not supported" in (result.error or "")
    assert pre.reserves == post.reserves


def test_cfamm_lp_actions_require_market_collateral_token():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000)
    ctx = ExecutionContext(agent_state=AgentState(agent_id="lp", balances={"USDC": 5_000}))

    result = market.execute(
        LPAction(agent_id="lp", collateral="USDC", amount=1_000, lp_type=LPActionType.DEPOSIT),
        ctx,
    )

    assert result.success is False
    assert "LP collateral must be COLLATERAL" in (result.error or "")
    assert market.get_lp_position("lp") is None


def test_population_builder_applies_role_params_to_builtins():
    collateral = Token(id="COLLATERAL", symbol="COL", decimals=0)
    agents = PopulationBuilder.build(
        PopulationConfig(
            mix={"noise": 1.0},
            total_agents=1,
            default_collateral=1_000,
            role_params={"noise": {"trade_min": 7, "trade_max": 9, "frequency": 1.0}},
        ),
        collateral_token=collateral,
    )

    noise = agents[0]
    assert noise.params.trade_min == 7
    assert noise.params.trade_max == 9
    assert noise.params.frequency == 1.0
    assert noise.state.balance("COLLATERAL") == 1_000


def test_population_builder_matches_total_agents():
    collateral = Token(id="COLLATERAL", symbol="COL", decimals=0)
    agents = PopulationBuilder.build(
        PopulationConfig(
            mix={"noise": 0.34, "arbitrageur": 0.33, "lp": 0.33},
            total_agents=5,
            default_collateral=1_000,
        ),
        collateral_token=collateral,
    )

    assert len(agents) == 5


def test_lp_fee_splits_are_distributed_in_fee_token_not_gas_token():
    tokens = [Token(id="YES", symbol="YES", decimals=0), Token(id="NO", symbol="NO", decimals=0)]
    market = CfammMarket(tokens=tokens, initial_liquidity=10_000)

    lp = ScriptedAgent(
        "lp",
        {1: [LPAction(agent_id="lp", collateral="COLLATERAL", amount=1_000, lp_type=LPActionType.DEPOSIT)]},
        balances={"COLLATERAL": 5_000},
    )
    trader = ScriptedAgent(
        "trader",
        {2: [SingleAssetAction(agent_id="trader", asset="YES", collateral="USDC", amount=1_000, side=Side.BUY)]},
        balances={"USDC": 5_000},
    )

    engine = SimulationEngine(
        market=market,
        agents=[lp, trader],
        config=SimulationConfig(
            num_rounds=2,
            execution_model=DirectExecution(ordering=FIFOOrdering(), cost_token="GAS"),
            default_fee_model=lambda gross, ctx: flat_fee(
                gross,
                ctx,
                trade_fee_bps=100,
                split_config={"lp": 10_000},
            ),
        ),
    )
    result = engine.run()

    assert result.agent_final_states["lp"].balance("USDC") == 10
    assert result.agent_final_states["lp"].balance("GAS") == 0


def test_fixed_rate_emission_respects_duration_and_remaining_supply():
    schedule = FixedRateEmission({"GOV": 10}, duration=5)

    assert schedule.rewards_for_period(0, 3) == {"GOV": 30}
    assert schedule.rewards_for_period(3, 10) == {"GOV": 20}
    assert schedule.rewards_for_period(10, 12) == {}
    assert schedule.total_remaining() == {"GOV": 0}


def test_decaying_emission_applies_decay_per_period_boundary():
    schedule = DecayingEmission({"GOV": 100}, decay_factor=0.5, decay_period=10)

    assert schedule.rewards_for_period(0, 15) == {"GOV": 1_250}
    assert schedule.rewards_for_period(15, 20) == {"GOV": 250}
    assert schedule.total_remaining() == {"GOV": 500}


def test_world_mode_applies_information_filter_to_all_market_states():
    world = World()
    world.add_market("amm", FixedStateMarket("RAW_AMM"))
    world.add_market("clob", FixedStateMarket("RAW_CLOB"))
    agent = RecordingWorldAgent("observer")

    engine = SimulationEngine(
        market=world,
        agents=[agent],
        config=SimulationConfig(
            num_rounds=1,
            execution_model=DirectExecution(ordering=FIFOOrdering()),
            information_filter=RedactingWorldFilter(),
        ),
    )
    engine.run()

    assert agent.seen_worlds == [{"amm": ["filtered:amm"], "clob": ["filtered:clob"]}]


def test_liquidation_phase_uses_initial_intents_without_second_decide():
    bus = EventBus(record_history=True)
    market = LiquidationRecordingMarket()
    liquidator = CountingLiquidatorAgent("bot")

    engine = SimulationEngine(
        market=market,
        agents=[liquidator],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )
    engine.run()

    assert liquidator.calls == 1
    assert market.liquidations == [("bot", "target")]
    liquidation_events = [event for event in bus.history if event.type == EventType.LIQUIDATION]
    assert len(liquidation_events) == 1
    assert liquidation_events[0].data["liquidator_id"] == "bot"


def test_metric_registry_streaming_metrics_receive_world_states():
    class ProbeMetric:
        def __init__(self):
            self.states: list[dict[str, MarketSnapshot] | MarketSnapshot | None] = []

        def on_round(self, round: int, timestamp: int, market_state):
            self.states.append(market_state)

        def finalize(self) -> float:
            return float(len(self.states))

    bus = EventBus()
    registry = MetricRegistry()
    probe = ProbeMetric()
    registry.register_streaming("probe", probe)
    registry.subscribe_to(bus)

    world = World()
    world.add_market("amm", FixedStateMarket("RAW_AMM"))
    world.add_market("clob", FixedStateMarket("RAW_CLOB"))

    engine = SimulationEngine(
        market=world,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )
    engine.run()

    assert isinstance(probe.states[0], dict)
    assert probe.states[0]["amm"].tokens == ["RAW_AMM"]


def test_parameter_changed_event_includes_previous_value():
    bus = EventBus(record_history=True)
    params = ParameterStore(defaults={"fee_bps": 30})
    params.schedule(ScheduledChange(key="fee_bps", value=45, execute_at_round=1))

    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[NoopAgent("observer")],
        config=SimulationConfig(
            num_rounds=1,
            parameters=params,
            execution_model=DirectExecution(ordering=FIFOOrdering()),
        ),
        event_bus=bus,
    )
    engine.run()

    event = next(event for event in bus.history if event.type == EventType.PARAMETER_CHANGED)
    assert event.data["old_value"] == 30
    assert event.data["new_value"] == 45


def test_epoch_boundary_is_emitted_on_round_one_when_epoch_changes():
    bus = EventBus(record_history=True)
    engine = SimulationEngine(
        market=RecordingMarket(),
        agents=[NoopAgent("observer")],
        config=SimulationConfig(
            num_rounds=1,
            clock=BlockClock(epoch_length=1),
            execution_model=DirectExecution(ordering=FIFOOrdering()),
        ),
        event_bus=bus,
    )
    engine.run()

    boundaries = [event for event in bus.history if event.type == EventType.EPOCH_BOUNDARY]
    assert len(boundaries) == 1
    assert boundaries[0].round == 1
    assert boundaries[0].data == {"epoch": 1, "prev_epoch": 0}


def test_dynamic_and_spread_fees_use_market_state():
    balanced = ExecutionContext(
        agent_state=AgentState(agent_id="alice"),
        market_state=AmmSnapshot(
            num_assets=2,
            tokens=["YES", "NO"],
            reserves={"YES": 1_000, "NO": 1_000},
            prices={"YES": 500, "NO": 500},
            total_liquidity=2_000,
            invariant=1,
        ),
    )
    imbalanced = ExecutionContext(
        agent_state=AgentState(agent_id="alice"),
        market_state=AmmSnapshot(
            num_assets=2,
            tokens=["YES", "NO"],
            reserves={"YES": 1_900, "NO": 100},
            prices={"YES": 950, "NO": 50},
            total_liquidity=2_000,
            invariant=1,
        ),
    )
    narrow_spread = ExecutionContext(
        agent_state=AgentState(agent_id="alice"),
        market_state=ClobSnapshot(
            num_assets=2,
            tokens=["ETH", "USDC"],
            best_bid={"ETH": 99},
            best_ask={"ETH": 100},
            spread={"ETH": 1},
            total_depth={"ETH": 100},
        ),
    )
    wide_spread = ExecutionContext(
        agent_state=AgentState(agent_id="alice"),
        market_state=ClobSnapshot(
            num_assets=2,
            tokens=["ETH", "USDC"],
            best_bid={"ETH": 80},
            best_ask={"ETH": 100},
            spread={"ETH": 20},
            total_depth={"ETH": 100},
        ),
    )

    assert dynamic_fee(100_000, imbalanced).total_fee > dynamic_fee(100_000, balanced).total_fee
    assert spread_fee(100_000, wide_spread).total_fee > spread_fee(100_000, narrow_spread).total_fee


def test_compute_slippage_uses_market_quote_token():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    quote = Token(id="USDC", symbol="USDC", decimals=0)
    market = ClobMarket(pairs=[(base, quote)])
    market._books[("ETH", "USDC")].place_order(
        Order(
            agent_id="maker",
            base="ETH",
            quote="USDC",
            side=OBSide.SELL,
            price=10,
            quantity=5,
            timestamp=0,
        ),
    )

    slippage = compute_slippage(market, "ETH", 0.5)

    assert 0.0 <= slippage < 1.0


def test_clob_market_order_uses_matching_quote_book_when_base_repeats():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    usdc = Token(id="USDC", symbol="USDC", decimals=0)
    dai = Token(id="DAI", symbol="DAI", decimals=0)
    market = ClobMarket(pairs=[(base, usdc), (base, dai)])

    maker_ctx = ExecutionContext(agent_state=AgentState(agent_id="maker", balances={"ETH": 5}))
    placed = market.execute(
        OrderAction(agent_id="maker", base="ETH", quote="DAI", side=OrderSide.SELL, price=10, quantity=5),
        maker_ctx,
    )
    assert placed.success

    taker_ctx = ExecutionContext(agent_state=AgentState(agent_id="taker", balances={"DAI": 100}))
    result = market.execute(
        SingleAssetAction(agent_id="taker", asset="ETH", collateral="DAI", amount=100, side=Side.BUY),
        taker_ctx,
    )

    assert result.success
    assert result.token_deltas["ETH"] == 5
    assert result.token_deltas["DAI"] == -50


def test_clob_state_uses_pair_keys_when_base_repeats():
    base = Token(id="ETH", symbol="ETH", decimals=0)
    usdc = Token(id="USDC", symbol="USDC", decimals=0)
    dai = Token(id="DAI", symbol="DAI", decimals=0)
    market = ClobMarket(pairs=[(base, usdc), (base, dai)])

    maker_ctx = ExecutionContext(agent_state=AgentState(agent_id="maker", balances={"ETH": 10}))
    assert market.execute(
        OrderAction(agent_id="maker", base="ETH", quote="USDC", side=OrderSide.SELL, price=11, quantity=5),
        maker_ctx,
    ).success
    assert market.execute(
        OrderAction(agent_id="maker", base="ETH", quote="DAI", side=OrderSide.SELL, price=9, quantity=5),
        maker_ctx,
    ).success

    state = market.get_state()
    prices = market.get_prices()

    assert "ETH" not in state.best_ask
    assert state.best_ask["ETH/USDC"] == 11
    assert state.best_ask["ETH/DAI"] == 9
    assert prices["ETH/USDC"] == 11
    assert prices["ETH/DAI"] == 9


def test_stochastic_feed_supports_additional_processes():
    mean_reverting = StochasticFeed("mean_reversion", {"initial": 100.0, "theta": 95.0, "sigma": 0.1}, seed=7)
    jump_diffusion = StochasticFeed("jump_diffusion", {"initial": 100.0, "jump_intensity": 0.5}, seed=11)

    assert mean_reverting.oracle_for("ETH").price_at(1)[0] > 0
    assert jump_diffusion.oracle_for("ETH").price_at(1)[0] > 0


def test_historical_feed_preserves_float_prices():
    feed = HistoricalFeed({"ETH": np.array([100.25, 101.5])})

    assert isinstance(feed.oracle_for("ETH").price_at(0)[0], float)
    assert feed.oracle_for("ETH").price_at(5)[0] == 101.5


def test_stochastic_feed_accepts_explicit_rng():
    params = {"initial": 100.0, "sigma": 0.05, "scale": 1}
    left = StochasticFeed("gbm", params, rng=np.random.default_rng(123))
    right = StochasticFeed("gbm", params, rng=np.random.default_rng(123))

    assert left.oracle_for("ETH").price_at(3)[0] == right.oracle_for("ETH").price_at(3)[0]


def test_engine_seed_drives_stochastic_feed_when_feed_seed_is_omitted():
    def run_once() -> dict[str, float | int] | None:
        feed = StochasticFeed("gbm", {"initial": 100.0, "sigma": 0.05, "scale": 1})
        engine = SimulationEngine(
            market=FixedStateMarket("ETH"),
            agents=[NoopAgent("observer")],
            config=SimulationConfig(num_rounds=1, seed=17, feeds=[feed]),
        )
        engine.run()
        return engine._last_feed_prices

    assert run_once() == run_once()


def test_run_sweep_uses_master_seed_and_generates_seeds_per_param_combo():
    df = run_sweep(
        SweepConfig(
            market_factory=lambda value: RecordingMarket(),
            agent_factory=lambda value: [NoopAgent(f"observer-{value}")],
            param_grid={"value": [1, 2]},
            num_runs=2,
            master_seed=123,
            num_rounds=1,
        )
    )

    master_rng = np.random.default_rng(123)
    expected = {
        1: [int(master_rng.integers(0, 2**31)) for _ in range(2)],
        2: [int(master_rng.integers(0, 2**31)) for _ in range(2)],
    }

    assert df.loc[df["value"] == 1, "seed"].tolist() == expected[1]
    assert df.loc[df["value"] == 2, "seed"].tolist() == expected[2]


def test_default_block_clock_is_network_neutral():
    clock = BlockClock()

    assert clock.block_time == 1
    assert clock.timestamp(1) == 1


def test_noise_trader_uses_unit_weight_scale_in_float_mode():
    agent = NoiseTrader(
        "noise",
        params=NoiseParams(collateral="USD", trade_min=10.0, trade_max=20.0, frequency=1.0, bundle_probability=1.0),
        rng=np.random.default_rng(1),
    )
    ctx = DecisionContext(
        market_state=MarketSnapshot(num_assets=2, tokens=["A", "B"]),
        agent_state=AgentState(agent_id="noise", balances={"USD": 100.0}),
        extra={"weight_scale": 1.0},
    )

    [action] = agent.decide(ctx)
    assert isinstance(action, BundleAction)
    assert abs(sum(action.weights.values()) - 1.0) < 1e-9


def test_threshold_predicate_supports_balance_path_alias():
    predicate = ThresholdPredicate(field="balance.USDC", source="agent", op=">=", threshold=100)
    agent_state = AgentState(agent_id="alice", balances={"USDC": 150})

    assert predicate.evaluate(MarketSnapshot(), agent_state) is True


def test_cfamm_lp_positions_report_true_share_fraction():
    tokens = [Token(id="A", symbol="A", decimals=9), Token(id="B", symbol="B", decimals=9)]
    market = CfammMarket(tokens=tokens, initial_liquidity=1_000 * tokens[0].scale)

    result = market.deposit_liquidity("lp_0", 100 * tokens[0].scale)

    assert result.success
    position = market.get_lp_position("lp_0")
    assert position is not None
    assert position.share_fraction == 100 * tokens[0].scale // 1_100


def test_world_emits_market_added_and_removed_events_after_engine_attach():
    bus = EventBus(record_history=True)
    world = World()
    world.add_market("amm", FixedStateMarket("A"))

    SimulationEngine(
        market=world,
        agents=[NoopAgent("observer")],
        config=SimulationConfig(num_rounds=1, execution_model=DirectExecution(ordering=FIFOOrdering())),
        event_bus=bus,
    )
    world.add_market("clob", FixedStateMarket("B"))
    world.remove_market("clob")

    assert [event.type for event in bus.history] == [EventType.MARKET_ADDED, EventType.MARKET_REMOVED]
