"""Two-protocol composition fork test (PRD US-003 line 705 / DoD line 723).

Forks **both** Whirlpool and DLMM at the same slot from the committed corpus
fixture under ``solana-plans/calibration/corpus/162000001/`` and asserts:

1. The :class:`ForkLoader` returns a single :class:`InitialState` that carries
   both protocols' pool fragments — i.e. a multi-protocol ``ForkSpec`` does
   *not* drop one parser's fragments under the other.
2. Each protocol's fragments match the manifest's expected per-pubkey values
   (sanity-check that the loader routed each pubkey through its own hydrator
   rather than collapsing everything through one parser shape).
3. ``build_forked_engine(...)`` produces a runnable
   :class:`SimulationEngine` whose hydrated ``World`` carries one named market
   per protocol model and whose ``run()`` advances rounds without errors. This
   pins PRD line 705's "engine can run forward without errors" half of the
   bullet — the framework must work for more than one parser shape on day one.

The committed fixture is intentionally synthetic; once 2.4 calibration pulls
real archival data, both program_accounts files plus the manifest's
``pool_reserves`` / ``pool_active_id`` sections should be replaced with
mainnet-derived values without changing the test shape.
"""

from __future__ import annotations

import re
from pathlib import Path

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
from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_builder import build_forked_engine
from defi_sim.engine.fork_execution import ForkExecution
from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.world import World
from defi_sim_solana.replay import account_client as account_client_mod
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim_solana.replay.dlmm_hydrator import DlmmStateHydrator
from defi_sim_solana.replay.whirlpool_hydrator import WhirlpoolStateHydrator

COMPOSE_CORPUS_SLOT = 162_000_001
WHIRLPOOL_PROTOCOL_MODEL = "Whirlpool"
DLMM_PROTOCOL_MODEL = "MeteoraDlmm"


@pytest.fixture(autouse=True)
def _reset_program_accounts_cache() -> None:
    account_client_mod.clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


# ---------------------------------------------------------------------------
# ForkableMarket subclasses for the composition test
# ---------------------------------------------------------------------------
# These are the smallest possible real ``ForkableMarket`` impls that satisfy
# ``build_forked_engine``'s materialization path: a working ``from_initial_state``
# and the ``Market`` shape (get_state / execute / copy / to_bytes / from_bytes)
# the simulation engine touches when ticking rounds. They do not model any
# CLMM math — that lands with the Phase 3 protocol implementations. The
# composition test only needs the two markets to coexist inside one World,
# carry the parsed fragments, and not crash when ``engine.run()`` ticks.


class _ComposeWhirlpoolMarket(Market, ForkableMarket):
    market_type = "compose_whirlpool"
    state_hydrator = WhirlpoolStateHydrator()

    def __init__(self, fragments: list[InitialStateFragment]) -> None:
        self.fragments = list(fragments)

    @classmethod
    def from_initial_state(cls, fragments, *, parameters, numeric_mode):
        return cls(list(fragments))

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["SOL", "USDC"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "_ComposeWhirlpoolMarket":
        return _ComposeWhirlpoolMarket(list(self.fragments))

    def to_bytes(self) -> bytes:
        return repr(sorted(f.pubkey for f in self.fragments)).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "_ComposeWhirlpoolMarket":
        return cls([])


class _ComposeDlmmMarket(Market, ForkableMarket):
    market_type = "compose_dlmm"
    state_hydrator = DlmmStateHydrator()

    def __init__(self, fragments: list[InitialStateFragment]) -> None:
        self.fragments = list(fragments)

    @classmethod
    def from_initial_state(cls, fragments, *, parameters, numeric_mode):
        return cls(list(fragments))

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["SOL", "USDC"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "_ComposeDlmmMarket":
        return _ComposeDlmmMarket(list(self.fragments))

    def to_bytes(self) -> bytes:
        return repr(sorted(f.pubkey for f in self.fragments)).encode()

    @classmethod
    def from_bytes(cls, data: bytes) -> "_ComposeDlmmMarket":
        return cls([])


class _SilentAgent(Agent):
    """Inert agent: tracks decide() calls and emits no actions."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)
        self.decide_calls = 0

    def decide(self, ctx: DecisionContext) -> list[Action]:
        self.decide_calls += 1
        return []


# ---------------------------------------------------------------------------
# Manifest reader (combined Whirlpool + DLMM sections at one slot)
# ---------------------------------------------------------------------------


def _expected_manifest_metrics(
    slot: int,
) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, int],
    dict[str, int],
]:
    """Read the combined manifest's expected sections.

    Returns ``(reserves, whirlpool_ticks, dlmm_active_ids)`` where
    ``reserves`` covers every pool pubkey under ``pool_reserves`` and the
    other two dicts hold the per-protocol scalar sections. The keys partition
    cleanly because the synthetic fixture uses disjoint pubkey conventions
    (Whirl* for Whirlpool, Dlmm* for DLMM).
    """
    manifest_path: Path = corpus_root() / str(slot) / "manifest.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    reserves: dict[str, tuple[int, int]] = {}
    ticks: dict[str, int] = {}
    active_ids: dict[str, int] = {}
    section: str | None = None
    list_re = re.compile(r'^\s*"([^"]+)"\s*:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*$')
    scalar_re = re.compile(r'^\s*"([^"]+)"\s*:\s*(-?\d+)\s*$')
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("  pool_reserves:"):
            section = "pool_reserves"
            continue
        if line.startswith("  pool_tick_current_index:"):
            section = "pool_tick_current_index"
            continue
        if line.startswith("  pool_active_id:"):
            section = "pool_active_id"
            continue
        if line.startswith("  ") and not line.startswith("    "):
            section = None
            continue
        if section == "pool_reserves":
            m = list_re.match(line)
            if m:
                reserves[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        elif section == "pool_tick_current_index":
            m = scalar_re.match(line)
            if m:
                ticks[m.group(1)] = int(m.group(2))
        elif section == "pool_active_id":
            m = scalar_re.match(line)
            if m:
                active_ids[m.group(1)] = int(m.group(2))
    return reserves, ticks, active_ids


def _build_loader() -> ForkLoader:
    """Corpus-only loader registered for both protocols.

    No historical backend: the corpus fixture at slot 162_000_001 carries
    program_accounts files for both programs, so
    ``get_program_accounts_at_slot`` resolves locally before any backend is
    consulted.
    """
    registry = ProtocolModelRegistry(
        {
            WHIRLPOOL_PROTOCOL_MODEL: _ComposeWhirlpoolMarket,
            DLMM_PROTOCOL_MODEL: _ComposeDlmmMarket,
        }
    )
    return ForkLoader(registry, historical_backend=None)


def _build_spec() -> ForkSpec:
    return ForkSpec(
        slot=COMPOSE_CORPUS_SLOT,
        protocols=[
            ProtocolForkRequest(protocol_model=WHIRLPOOL_PROTOCOL_MODEL),
            ProtocolForkRequest(protocol_model=DLMM_PROTOCOL_MODEL),
        ],
    )


# ---------------------------------------------------------------------------
# The composition test (PRD line 705)
# ---------------------------------------------------------------------------


def test_fork_compose_whirlpool_and_dlmm() -> None:
    """PRD US-003 line 705 — fork Whirlpool and DLMM at the same slot, assert
    both protocols have their expected accounts and the engine can run forward
    without errors.

    Three claims pinned together (deliberately one test for line 705 even
    though the bullet enumerates two halves: "expected accounts" + "run
    forward"). Splitting them would duplicate the fixture wiring; pinning
    them in one test makes the regression signal "the composition path is
    broken" instead of "one of two unrelated tests on the same fixture
    flipped red".
    """
    expected_reserves, expected_ticks, expected_active_ids = (
        _expected_manifest_metrics(COMPOSE_CORPUS_SLOT)
    )
    expected_whirlpool_pubkeys = set(expected_ticks)
    expected_dlmm_pubkeys = set(expected_active_ids)
    assert expected_whirlpool_pubkeys, (
        f"manifest.yaml at slot {COMPOSE_CORPUS_SLOT} must declare at least "
        "one Whirlpool pool under pool_tick_current_index."
    )
    assert expected_dlmm_pubkeys, (
        f"manifest.yaml at slot {COMPOSE_CORPUS_SLOT} must declare at least "
        "one DLMM pool under pool_active_id."
    )
    assert expected_whirlpool_pubkeys.isdisjoint(expected_dlmm_pubkeys), (
        "synthetic composition fixture must use disjoint Whirlpool / DLMM "
        "pubkey conventions; reusing a pubkey across protocols would make "
        "the per-protocol assertion ambiguous."
    )

    # 1. Loader-level: both protocols' fragments arrive in one InitialState.
    loader = _build_loader()
    initial = loader.load(_build_spec())

    assert isinstance(initial, InitialState)
    assert initial.slot == COMPOSE_CORPUS_SLOT

    whirlpool_fragments = initial.by_protocol(WHIRLPOOL_PROTOCOL_MODEL)
    dlmm_fragments = initial.by_protocol(DLMM_PROTOCOL_MODEL)
    assert {f.pubkey for f in whirlpool_fragments} == expected_whirlpool_pubkeys, (
        "Whirlpool fragment pubkeys do not match the manifest's expected set; "
        "the composition path may have collapsed both protocols through one "
        "hydrator."
    )
    assert {f.pubkey for f in dlmm_fragments} == expected_dlmm_pubkeys, (
        "DLMM fragment pubkeys do not match the manifest's expected set."
    )

    # 2. Per-protocol payload sanity: each pubkey routed through the right
    # hydrator (Whirlpool fragments expose tick_current_index; DLMM fragments
    # expose active_id; cross-protocol leakage would fail one of these).
    for fragment in whirlpool_fragments:
        assert fragment.kind == "pool"
        payload = fragment.payload
        assert tuple(payload["reserve_proxy"]) == expected_reserves[fragment.pubkey]
        assert int(payload["tick_current_index"]) == expected_ticks[fragment.pubkey]
        assert "active_id" not in payload
    for fragment in dlmm_fragments:
        assert fragment.kind == "pool"
        payload = fragment.payload
        assert tuple(payload["reserve_proxy"]) == expected_reserves[fragment.pubkey]
        assert int(payload["active_id"]) == expected_active_ids[fragment.pubkey]
        assert "tick_current_index" not in payload

    # 3. Engine-level: build_forked_engine wires through both protocols and
    # ticks rounds without raising. The synthetic agent emits no actions; the
    # assertion is structural (engine constructed) + runtime (run() returns).
    agent = _SilentAgent("synth")
    config = SimulationConfig(num_rounds=2)
    engine = build_forked_engine(
        fork_spec=_build_spec(),
        fork_loader=_build_loader(),
        registry=ProtocolModelRegistry(
            {
                WHIRLPOOL_PROTOCOL_MODEL: _ComposeWhirlpoolMarket,
                DLMM_PROTOCOL_MODEL: _ComposeDlmmMarket,
            }
        ),
        agents=[agent],
        config=config,
    )

    assert isinstance(engine._market, World)
    assert WHIRLPOOL_PROTOCOL_MODEL in engine._market.markets
    assert DLMM_PROTOCOL_MODEL in engine._market.markets
    whirl_market = engine._market.get_market(WHIRLPOOL_PROTOCOL_MODEL)
    dlmm_market = engine._market.get_market(DLMM_PROTOCOL_MODEL)
    assert isinstance(whirl_market, _ComposeWhirlpoolMarket)
    assert isinstance(dlmm_market, _ComposeDlmmMarket)
    assert {f.pubkey for f in whirl_market.fragments} == expected_whirlpool_pubkeys
    assert {f.pubkey for f in dlmm_market.fragments} == expected_dlmm_pubkeys

    assert isinstance(engine._execution_model, ForkExecution)
    assert engine._execution_model.start_slot == COMPOSE_CORPUS_SLOT

    result = engine.run()
    assert agent.decide_calls == 2
    assert engine.current_round == 2
    assert result.num_rounds_executed == 2
