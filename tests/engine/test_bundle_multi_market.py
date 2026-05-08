"""Multi-market bundle scope guard (PRD US-011 line 866 decision).

The 1.7 DoD records the chosen path: world-mode ``MultiMarketAction``
routing lives inside the bundle execution helper. ``_execute_bundle_atomically``
walks bundle actions through the standard ``_execute_action`` dispatch
under a single ``atomic_state_boundary``, so each ``MultiMarketAction``
unwraps onto its target market while the rollback boundary still spans
the whole bundle. These tests pin that contract: bundles can route to
multiple markets atomically, and a revert at any position rolls back
state across every market the bundle touched.
"""

from __future__ import annotations

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.market import Liquidatable, Market
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
from defi_sim.engine.execution import DirectExecution
from defi_sim.engine.ordering import FIFOOrdering
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.world import World


class _RecordingMarket(Market, Liquidatable):
    market_type = "recording"

    def __init__(self, *, fail_for: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail_for = fail_for or set()

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["DEBT", "COLLATERAL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        if not isinstance(action, LiquidateAction):
            return ExecutionResult(success=False, error="unsupported")
        if action.target_agent_id in self._fail_for:
            return ExecutionResult(success=False, error="forced failure")
        self.calls.append((action.agent_id, action.target_agent_id))
        return ExecutionResult(success=True)

    def copy(self) -> "_RecordingMarket":
        clone = _RecordingMarket(fail_for=set(self._fail_for))
        clone.calls = list(self.calls)
        return clone

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "_RecordingMarket":
        return cls()

    def get_liquidatable_agents(self) -> list[str]:
        return ["target", "target-2"]

    def compute_liquidation_bonus(self, agent_id: str, repay_amount: int) -> int:
        return repay_amount


class _NoopAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []


def _engine_with_two_markets(
    *,
    fail_on_m2: bool = False,
) -> tuple[SimulationEngine, World]:
    world = World()
    world.add_market("m1", _RecordingMarket())
    world.add_market(
        "m2",
        _RecordingMarket(fail_for={"target-2"} if fail_on_m2 else None),
    )
    config = SimulationConfig(
        num_rounds=1,
        execution_model=DirectExecution(ordering=FIFOOrdering()),
    )
    engine = SimulationEngine(world, [_NoopAgent("bot")], config)
    return engine, world


def _calls(world: World, market_name: str) -> list[tuple[str, str]]:
    market = world.get_market(market_name)
    assert isinstance(market, _RecordingMarket)
    return market.calls


def _liquidate(market_name: str, target: str) -> MultiMarketAction:
    return MultiMarketAction(
        agent_id="bot",
        market_name=market_name,
        inner=LiquidateAction(
            agent_id="bot",
            target_agent_id=target,
            repay_token="DEBT",
            repay_amount=5,
            seize_token="COLLATERAL",
        ),
    )


def test_multi_market_bundle_lands_on_both_markets() -> None:
    engine, world = _engine_with_two_markets()

    bundle = [_liquidate("m1", "target"), _liquidate("m2", "target")]
    outcome = engine._execute_bundle_atomically(bundle, round_num=0, ts=0)

    assert outcome["reverted"] is False
    assert outcome["failed_at_index"] is None
    assert len(outcome["executed"]) == 2
    assert _calls(world, "m1") == [("bot", "target")]
    assert _calls(world, "m2") == [("bot", "target")]


def test_multi_market_bundle_atomic_revert() -> None:
    """A revert in the second-market action rolls back the first market too."""
    engine, world = _engine_with_two_markets(fail_on_m2=True)

    bundle = [_liquidate("m1", "target"), _liquidate("m2", "target-2")]
    outcome = engine._execute_bundle_atomically(bundle, round_num=0, ts=0)

    assert outcome["reverted"] is True
    assert outcome["failed_at_index"] == 1
    assert outcome["failed_reason"]
    assert outcome["executed"] == []
    assert _calls(world, "m1") == []
    assert _calls(world, "m2") == []
