"""Tests for the ``ForkableMarket`` / ``SeedableAgent`` contracts (PRD US-003 line 447)."""

from __future__ import annotations

import pytest

from defi_sim.core.types import FLOAT_MODE
from defi_sim.engine.forkable import ForkableMarket, SeedableAgent
from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator


class _FakeHydrator(StateHydrator):
    program_id = "FakeProgram"
    schema_version = 1

    def account_filters(self) -> list[AccountFilter]:
        return [AccountFilter(discriminator=b"\x00" * 8)]

    def parse_account(self, pubkey, data):  # type: ignore[override]
        return InitialStateFragment(
            kind="pool",
            protocol_model="fakepool",
            pubkey=pubkey,
            owner=None,
            payload={"data_len": len(data)},
        )


class _FakeForkableMarket(ForkableMarket):
    state_hydrator = _FakeHydrator()

    def __init__(self, fragments: list[InitialStateFragment]) -> None:
        self.fragments = list(fragments)

    @classmethod
    def from_initial_state(
        cls,
        fragments,
        *,
        parameters,
        numeric_mode,
    ):
        return cls(fragments)


class _FakeSeedableAgent(SeedableAgent):
    def __init__(self) -> None:
        self.seeded: list[InitialStateFragment] = []

    def seed_from_fragments(self, fragments):
        self.seeded.extend(fragments)


def _wallet_fragment(owner: str, mint: str, amount: int) -> InitialStateFragment:
    return InitialStateFragment(
        kind="wallet_balance",
        protocol_model="spl_token",
        pubkey=f"{owner}-{mint}",
        owner=owner,
        payload={"mint": mint, "amount": amount},
    )


def _pool_fragment(pubkey: str) -> InitialStateFragment:
    return InitialStateFragment(
        kind="pool",
        protocol_model="fakepool",
        pubkey=pubkey,
        owner=None,
        payload={},
    )


def test_forkable_market_cannot_be_instantiated_without_factory() -> None:
    with pytest.raises(TypeError):
        ForkableMarket()  # type: ignore[abstract]


def test_seedable_agent_cannot_be_instantiated_without_hook() -> None:
    with pytest.raises(TypeError):
        SeedableAgent()  # type: ignore[abstract]


def test_partial_forkable_market_cannot_instantiate() -> None:
    class _Partial(ForkableMarket):
        state_hydrator = _FakeHydrator()
        # missing from_initial_state

    with pytest.raises(TypeError):
        _Partial()  # type: ignore[abstract]


def test_partial_seedable_agent_cannot_instantiate() -> None:
    class _Partial(SeedableAgent):
        pass  # missing seed_from_fragments

    with pytest.raises(TypeError):
        _Partial()  # type: ignore[abstract]


def test_forkable_market_factory_consumes_fragments() -> None:
    fragments = [_pool_fragment("PoolA"), _pool_fragment("PoolB")]
    parameters = ParameterStore()
    market = _FakeForkableMarket.from_initial_state(
        fragments,
        parameters=parameters,
        numeric_mode=FLOAT_MODE,
    )
    assert isinstance(market, _FakeForkableMarket)
    assert isinstance(market, ForkableMarket)
    assert [f.pubkey for f in market.fragments] == ["PoolA", "PoolB"]


def test_forkable_market_exposes_state_hydrator_class_attr() -> None:
    assert isinstance(_FakeForkableMarket.state_hydrator, StateHydrator)
    assert _FakeForkableMarket.state_hydrator.program_id == "FakeProgram"


def test_seedable_agent_seeds_from_owner_scoped_fragments() -> None:
    agent = _FakeSeedableAgent()
    frags = [
        _wallet_fragment("OwnerA", "USDC", 100),
        _wallet_fragment("OwnerA", "SOL", 5),
    ]
    agent.seed_from_fragments(frags)
    assert len(agent.seeded) == 2
    assert {f.payload["mint"] for f in agent.seeded} == {"USDC", "SOL"}
    assert {f.owner for f in agent.seeded} == {"OwnerA"}
