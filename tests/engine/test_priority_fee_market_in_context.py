"""PRD US-010 line 744: ``engine.priority_fee_market`` is accessible to agents
via the ``DecisionContext``.

Verifies that:
- ``SimulationEngine.priority_fee_market`` returns the underlying execution
  model's market when the model is Solana-aware (``SolanaLikeExecution``).
- Each agent's ``DecisionContext`` carries the same market instance the
  execution model is updating, so observing a write-locked account from
  inside a slot is visible to agents on the next round's decide().
- Returns ``None`` for non-Solana execution models (``DirectExecution``).
"""

from __future__ import annotations

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.market import Market
from defi_sim.core.types import (
    Action,
    AgentState,
    ExecutionContext,
    ExecutionResult,
    MarketSnapshot,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.execution import DirectExecution, SolanaLikeExecution
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.simulation import SimulationEngine


class _NoopMarket(Market):
    market_type = "noop"

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["USDC", "SOL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "_NoopMarket":
        return _NoopMarket()

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "_NoopMarket":
        return cls()


class _CapturingAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self.captured: list[DecisionContext] = []

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.captured.append(ctx)
        return []


def test_engine_priority_fee_market_property_on_solana_like() -> None:
    market = PriorityFeeMarket(floor_micro_lamports=42)
    model = SolanaLikeExecution(priority_fee_market=market)
    engine = SimulationEngine(
        _NoopMarket(),
        [_CapturingAgent("a")],
        SimulationConfig(num_rounds=1, execution_model=model),
    )
    assert engine.priority_fee_market is market


def test_engine_priority_fee_market_is_none_for_non_solana_execution() -> None:
    engine = SimulationEngine(
        _NoopMarket(),
        [_CapturingAgent("a")],
        SimulationConfig(num_rounds=1, execution_model=DirectExecution()),
    )
    assert engine.priority_fee_market is None


def test_decision_context_exposes_priority_fee_market_to_agents() -> None:
    """Agents see the same market instance the engine wired in."""
    market = PriorityFeeMarket(floor_micro_lamports=99)
    agent = _CapturingAgent("trader")
    engine = SimulationEngine(
        _NoopMarket(),
        [agent],
        SimulationConfig(
            num_rounds=1,
            execution_model=SolanaLikeExecution(priority_fee_market=market),
        ),
    )
    engine.run()

    assert agent.captured, "agent.decide() should have been called"
    ctx = agent.captured[0]
    assert ctx.priority_fee_market is market
    # Floor passes through unchanged: agent can quote unseen accounts.
    assert ctx.priority_fee_market.quote("never_seen_pool", 50) == 99


def test_decision_context_priority_fee_market_is_none_under_direct_execution() -> None:
    agent = _CapturingAgent("trader")
    engine = SimulationEngine(
        _NoopMarket(),
        [agent],
        SimulationConfig(num_rounds=1, execution_model=DirectExecution()),
    )
    engine.run()

    assert agent.captured
    assert agent.captured[0].priority_fee_market is None
