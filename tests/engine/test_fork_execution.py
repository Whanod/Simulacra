"""Tests for :class:`ForkExecution` (PRD US-003 line 686)."""

from __future__ import annotations

import base64

import pytest

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
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_builder import build_forked_engine
from defi_sim.engine.fork_execution import ForkExecution
from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.gas import ComputeUnitCost
from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.ordering import PriorityOrdering
from defi_sim.engine.scheduler import PriorityScheduler
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator
from defi_sim.engine.world import World
from defi_sim_solana.replay import account_client as account_client_mod


@pytest.fixture(autouse=True)
def _reset_program_accounts_cache() -> None:
    account_client_mod.clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


def test_fork_execution_carries_only_start_slot_metadata():
    """PRD line 688: ``ForkExecution`` must not own protocol state.

    Regression guard against the "execution model owns protocol state"
    anti-pattern. Fork state lives on the ``World`` / ``Market`` produced
    by ``materialize_fork``; the execution model only carries the start
    slot for telemetry / cost-model selection.
    """
    fe = ForkExecution(start_slot=420_196_842)

    assert isinstance(fe, SolanaLikeExecution)
    assert fe.start_slot == 420_196_842

    for forbidden in ("state", "_state", "initial_state", "world"):
        assert not hasattr(fe, forbidden), (
            f"ForkExecution must not expose {forbidden!r} — fork state "
            "lives on the World/Market built by materialize_fork()."
        )


def test_fork_execution_inherits_solana_scheduler():
    """PRD line 689: priority ordering + compute-unit cost defaults survive.

    ``ForkExecution`` is a ``SolanaLikeExecution`` preset whose only
    additive responsibility is the ``start_slot`` tag. The Solana-like
    defaults (priority-aware ordering, compute-unit cost model, parallel
    priority scheduler) MUST be reachable on a ``ForkExecution`` built
    with only ``start_slot`` so a forked engine inherits Solana fidelity
    without any extra wiring at the call site.
    """
    fe = ForkExecution(start_slot=420_196_842)

    assert isinstance(fe._ordering, PriorityOrdering)
    assert isinstance(fe._cost_model, ComputeUnitCost)
    assert isinstance(fe._scheduler, PriorityScheduler)
    assert fe.supports_slot_execution() is True


# ---------------------------------------------------------------------------
# build_forked_engine — full pipeline (PRD line 690)
# ---------------------------------------------------------------------------


class _FakePoolHydrator(StateHydrator):
    program_id = "FakeFork111111111111111111111111111111111111"
    schema_version = 1
    _disc = b"\xa1" * 8

    def account_filters(self) -> list[AccountFilter]:
        return [AccountFilter(discriminator=self._disc)]

    def parse_account(self, pubkey, data):  # type: ignore[override]
        return InitialStateFragment(
            kind="pool",
            protocol_model="fakepool",
            pubkey=pubkey,
            owner=None,
            payload={"data_len": len(data)},
        )


class _FakeForkPool(Market, ForkableMarket):
    market_type = "fake_fork_pool"
    state_hydrator = _FakePoolHydrator()

    def __init__(self, fragments: list[InitialStateFragment]) -> None:
        self.fragments = list(fragments)
        self.executed_actions: list[Action] = []

    @classmethod
    def from_initial_state(
        cls,
        fragments,
        *,
        parameters,
        numeric_mode,
    ):
        return cls(list(fragments))

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["SOL", "USDC"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.executed_actions.append(action)
        return ExecutionResult(success=True)

    def copy(self) -> "_FakeForkPool":
        return _FakeForkPool(list(self.fragments))

    def to_bytes(self) -> bytes:
        return repr(sorted(f.pubkey for f in self.fragments)).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "_FakeForkPool":
        return cls([])


class _SyntheticAgent(Agent):
    """Tracks decide() calls but emits no actions — synthetic and inert."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self.decide_calls = 0

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.decide_calls += 1
        return []


class _FakeBackend:
    endpoint = "fake://forkbuilder"

    def __init__(self, accounts: list[dict]) -> None:
        self._accounts = accounts

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict:
        return {"program_id": program_id, "slot": slot, "accounts": self._accounts}


def _account(pubkey: str, raw_bytes: bytes) -> dict:
    return {
        "pubkey": pubkey,
        "account": {
            "owner": _FakePoolHydrator.program_id,
            "lamports": 1,
            "data": [base64.b64encode(raw_bytes).decode("ascii"), "base64"],
        },
    }


def test_build_forked_engine_runs_forward_with_synthetic_agents() -> None:
    """PRD line 690: full pipeline ``ForkSpec -> InitialState -> World ->
    SimulationEngine`` produces a runnable engine that ticks rounds.

    Pins three claims the helper must satisfy at once:
    (1) the engine receives a hydrated ``World`` (not an execution-model-with-
    secret-state) — ``engine._market`` is the ``World`` produced by
    ``materialize_fork``, and the constructed ``ForkableMarket`` is registered
    inside it under its protocol_model name;
    (2) the execution model is a ``ForkExecution`` carrying the spec slot —
    the legacy "execution model owns state" anti-pattern stays out;
    (3) ``engine.run()`` actually advances rounds with synthetic agents and
    drives them through ``decide()`` — the wiring is end-to-end runnable, not
    just constructible.
    """
    spec = ForkSpec(
        slot=420_196_842,
        protocols=[ProtocolForkRequest(protocol_model="fakepool")],
    )
    backend = _FakeBackend(
        [_account("PoolA", b"\x01\x02"), _account("PoolB", b"\x03")]
    )
    registry = ProtocolModelRegistry({"fakepool": _FakeForkPool})
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )
    agent = _SyntheticAgent("synth")
    config = SimulationConfig(num_rounds=2)

    engine = build_forked_engine(
        fork_spec=spec,
        fork_loader=loader,
        registry=registry,
        agents=[agent],
        config=config,
    )

    assert isinstance(engine._market, World)
    assert "fakepool" in engine._market.markets
    market = engine._market.get_market("fakepool")
    assert isinstance(market, _FakeForkPool)
    assert [f.pubkey for f in market.fragments] == ["PoolA", "PoolB"]

    assert isinstance(engine._execution_model, ForkExecution)
    assert engine._execution_model.start_slot == 420_196_842

    result = engine.run()

    assert agent.decide_calls == 2
    assert engine.current_round == 2
    assert result.num_rounds_executed == 2


def test_build_forked_engine_does_not_replay_historical_actions() -> None:
    """PRD line 691: only synthetic agent actions appear in the round log.

    Forking provides starting state — it must NOT smuggle in transactions
    pulled from the historical block at ``start_slot``. Regression guard
    against a future "we have the historical block, why not replay it?"
    refactor that would silently turn forward simulation into mainnet
    replay.

    Setup mirrors the line-690 test: two pool accounts hydrated from a
    fake backend at slot 420_196_842. The agent emits zero actions; the
    market records every ``execute()`` call. After running forward for
    multiple rounds, the market must have seen NO actions — every action
    that would have hit ``execute()`` would have had to come from
    somewhere other than the (silent) synthetic agent.
    """
    spec = ForkSpec(
        slot=420_196_842,
        protocols=[ProtocolForkRequest(protocol_model="fakepool")],
    )
    backend = _FakeBackend(
        [_account("PoolA", b"\x01\x02"), _account("PoolB", b"\x03")]
    )
    registry = ProtocolModelRegistry({"fakepool": _FakeForkPool})
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )
    agent = _SyntheticAgent("synth")
    config = SimulationConfig(num_rounds=3)

    engine = build_forked_engine(
        fork_spec=spec,
        fork_loader=loader,
        registry=registry,
        agents=[agent],
        config=config,
    )

    engine.run()

    market = engine._market.get_market("fakepool")
    assert isinstance(market, _FakeForkPool)
    # Loop actually ran (so an empty action log is meaningful, not vacuous):
    assert agent.decide_calls == 3
    # No action ever reached the market — no synthetic agent emitted one,
    # and no historical txn was replayed in their place.
    assert market.executed_actions == []
