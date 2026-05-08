"""Tests for ``materialize_fork`` + :class:`HydratedFork` (PRD US-003 line 543)."""

from __future__ import annotations

import pytest

from defi_sim.core.types import FLOAT_MODE
from defi_sim.engine.fork_hydration import (
    AgentStateSeed,
    HydratedFork,
    PriceFeedRegistry,
    materialize_fork,
)
from defi_sim.engine.fork_loader import ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator
from defi_sim.engine.world import World


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
            payload={},
        )


class _FakeForkableMarket(ForkableMarket):
    state_hydrator = _FakeHydrator()

    def __init__(
        self,
        fragments: list[InitialStateFragment],
        *,
        parameters=None,
        numeric_mode=None,
    ) -> None:
        self.fragments = list(fragments)
        self._captured_parameters = parameters
        self._captured_numeric_mode = numeric_mode

    @classmethod
    def from_initial_state(
        cls,
        fragments,
        *,
        parameters,
        numeric_mode,
    ):
        return cls(fragments, parameters=parameters, numeric_mode=numeric_mode)

    def get_state(self):
        return {"pubkeys": [f.pubkey for f in self.fragments]}

    def to_bytes(self) -> bytes:
        return repr(sorted(f.pubkey for f in self.fragments)).encode()


class _SecondFakeForkableMarket(_FakeForkableMarket):
    state_hydrator = _FakeHydrator()


def _pool(protocol: str, pubkey: str) -> InitialStateFragment:
    return InitialStateFragment(
        kind="pool",
        protocol_model=protocol,
        pubkey=pubkey,
        owner=None,
        payload={},
    )


def _oracle(pubkey: str, price: float) -> InitialStateFragment:
    return InitialStateFragment(
        kind="oracle_price",
        protocol_model="pyth",
        pubkey=pubkey,
        owner=None,
        payload={"price": price},
    )


def _wallet(owner: str, mint: str, amount: int) -> InitialStateFragment:
    return InitialStateFragment(
        kind="wallet_balance",
        protocol_model="spl_token",
        pubkey=f"{owner}-{mint}",
        owner=owner,
        payload={"mint": mint, "amount": amount},
    )


def _wallet_position(owner: str, market: str) -> InitialStateFragment:
    return InitialStateFragment(
        kind="wallet_position",
        protocol_model="marginfi",
        pubkey=f"{owner}-{market}-pos",
        owner=owner,
        payload={"market": market},
    )


def _registry(**models) -> ProtocolModelRegistry:
    return ProtocolModelRegistry(models)


def test_materialize_fork_returns_hydrated_fork() -> None:
    initial = InitialState(slot=250_000_000)
    initial.merge(_pool("fakepool", "PoolA"))
    registry = _registry(fakepool=_FakeForkableMarket)

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert isinstance(hydrated, HydratedFork)
    assert hydrated.start_slot == 250_000_000
    assert isinstance(hydrated.world, World)
    assert isinstance(hydrated.price_feeds, PriceFeedRegistry)


def test_materialize_fork_constructs_market_via_factory() -> None:
    initial = InitialState(slot=1)
    initial.merge(_pool("fakepool", "PoolA"))
    initial.merge(_pool("fakepool", "PoolB"))
    registry = _registry(fakepool=_FakeForkableMarket)
    parameters = ParameterStore()

    hydrated = materialize_fork(
        initial, registry, parameters=parameters, numeric_mode=FLOAT_MODE
    )

    assert "fakepool" in hydrated.world.markets
    market = hydrated.world.get_market("fakepool")
    assert isinstance(market, _FakeForkableMarket)
    assert [f.pubkey for f in market.fragments] == ["PoolA", "PoolB"]
    assert market._captured_parameters is parameters
    assert market._captured_numeric_mode is FLOAT_MODE


def test_materialize_fork_routes_pool_fragments_to_market() -> None:
    """PRD line 679: pool fragments end up inside a constructed Market,
    queryable via the market's public ``get_state()`` interface."""
    initial = InitialState(slot=1)
    initial.merge(_pool("fakepool", "PoolA"))
    initial.merge(_pool("fakepool", "PoolB"))
    registry = _registry(fakepool=_FakeForkableMarket)

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    market = next(iter(hydrated.world.markets.values()))
    state = market.get_state()
    assert state == {"pubkeys": ["PoolA", "PoolB"]}


def test_materialize_fork_one_market_per_protocol() -> None:
    initial = InitialState(slot=1)
    initial.merge(_pool("fakepool", "PoolA"))
    initial.merge(_pool("otherpool", "PoolB"))
    registry = _registry(
        fakepool=_FakeForkableMarket, otherpool=_SecondFakeForkableMarket
    )

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert set(hydrated.world.markets) == {"fakepool", "otherpool"}


def test_materialize_fork_routes_oracle_fragments_to_price_feeds() -> None:
    initial = InitialState(slot=1)
    initial.merge(_pool("fakepool", "PoolA"))
    initial.merge(_oracle("OraclePyth", 100.0))
    initial.merge(_oracle("OraclePyth2", 200.0))
    registry = _registry(fakepool=_FakeForkableMarket)

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert len(hydrated.price_feeds.fragments) == 2
    assert {f.pubkey for f in hydrated.price_feeds.fragments} == {
        "OraclePyth",
        "OraclePyth2",
    }


def test_materialize_fork_routes_oracle_fragments_to_price_feed_registry() -> None:
    """PRD line 680: ``oracle_price`` fragments populate the price-feed registry,
    not a Market. Strengthens the routing claim from
    ``test_materialize_fork_routes_oracle_fragments_to_price_feeds`` by also
    asserting oracle pubkeys never leak into any constructed Market's state.
    """
    initial = InitialState(slot=1)
    initial.merge(_pool("fakepool", "PoolA"))
    initial.merge(_oracle("OraclePyth", 100.0))
    initial.merge(_oracle("OraclePyth2", 200.0))
    registry = _registry(fakepool=_FakeForkableMarket)

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    oracle_pubkeys = {"OraclePyth", "OraclePyth2"}

    assert oracle_pubkeys.issubset(
        {f.pubkey for f in hydrated.price_feeds.fragments}
    )

    assert "pyth" not in hydrated.world.markets
    for market in hydrated.world.markets.values():
        market_pubkeys = set(market.get_state().get("pubkeys", []))
        assert market_pubkeys.isdisjoint(oracle_pubkeys)


def test_materialize_fork_groups_wallet_fragments_by_owner() -> None:
    initial = InitialState(slot=1)
    initial.merge(_wallet("OwnerA", "USDC", 100))
    initial.merge(_wallet("OwnerA", "SOL", 5))
    initial.merge(_wallet("OwnerB", "USDC", 50))
    initial.merge(_wallet_position("OwnerA", "marginfi-1"))
    registry = _registry()

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert set(hydrated.agent_seeds) == {"OwnerA", "OwnerB"}
    seed_a = hydrated.agent_seeds["OwnerA"]
    assert isinstance(seed_a, AgentStateSeed)
    assert seed_a.owner == "OwnerA"
    assert len(seed_a.fragments) == 3
    assert {f.pubkey for f in seed_a.fragments} == {
        "OwnerA-USDC",
        "OwnerA-SOL",
        "OwnerA-marginfi-1-pos",
    }
    assert len(hydrated.agent_seeds["OwnerB"].fragments) == 1


def test_materialize_fork_skips_protocols_without_market_fragments() -> None:
    initial = InitialState(slot=1)
    initial.merge(_oracle("OraclePyth", 100.0))
    registry = _registry()

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert hydrated.world.markets == {}
    assert len(hydrated.price_feeds.fragments) == 1


def test_materialize_fork_skips_protocols_with_no_market_fragments() -> None:
    """PRD line 682: a fork that only pulls oracle accounts produces an
    empty ``World`` (no markets), not a crash.

    Pins two claims the existing
    ``test_materialize_fork_skips_protocols_without_market_fragments`` covers
    only implicitly: (1) ``materialize_fork`` does not raise when the only
    fragments are oracle fragments and the registry has no entries that match,
    and (2) the resulting ``world.markets`` is the empty mapping (not ``None``,
    not a sentinel, not a crash). The empty-state degenerate case is covered
    elsewhere; this test specifically pins the "oracle-only fork" path.
    """
    initial = InitialState(slot=250_000_000)
    initial.merge(_oracle("OraclePyth-SOL", 100.0))
    initial.merge(_oracle("OraclePyth-USDC", 1.0))
    registry = _registry()

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert hydrated.world.markets == {}
    assert isinstance(hydrated.world, World)
    assert len(hydrated.price_feeds.fragments) == 2


def test_market_from_initial_state_is_idempotent() -> None:
    """PRD line 683: ``Market.from_initial_state(fragments).to_bytes()`` is
    deterministic — two independent constructions from the same fragments
    serialize to identical bytes.

    Pins the materialization-side of the fork-cache contract: a saved fork
    re-hydrated twice must produce byte-equal markets, otherwise downstream
    diffs (replay error bands, snapshot equality) would be non-deterministic
    even when the input state is byte-identical.
    """
    fragments = [
        _pool("fakepool", "PoolA"),
        _pool("fakepool", "PoolB"),
        _pool("fakepool", "PoolC"),
    ]
    parameters = ParameterStore()

    first = _FakeForkableMarket.from_initial_state(
        fragments, parameters=parameters, numeric_mode=FLOAT_MODE
    )
    second = _FakeForkableMarket.from_initial_state(
        fragments, parameters=parameters, numeric_mode=FLOAT_MODE
    )

    assert first is not second
    assert first.to_bytes() == second.to_bytes()


def test_initial_state_is_cacheable_value() -> None:
    """PRD line 684: ``InitialState`` round-trips through JSON; same fragments
    produce equal ``InitialState``.

    Pins the cache contract from PRD line 526: the parsed-state cache stores
    ``InitialState`` keyed by ``(slot, fork_spec_hash, hydrator_versions)``,
    so the value must be a deterministic, JSON-serializable value object —
    two builds from the same fragments compare equal, and a JSON round-trip
    is byte-identical to the original after parse.
    """
    fragments = [
        _pool("fakepool", "PoolA"),
        _oracle("OraclePyth", 100.0),
        _wallet("OwnerA", "USDC", 100),
    ]

    original = InitialState(slot=250_000_000, fragments=list(fragments))
    twin = InitialState(slot=250_000_000, fragments=list(fragments))
    assert twin == original

    restored = InitialState.from_json(original.to_json())
    assert restored == original
    assert restored.to_json() == original.to_json()


def test_materialize_fork_empty_initial_state_yields_empty_fork() -> None:
    initial = InitialState(slot=42)
    registry = _registry()

    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    assert hydrated.world.markets == {}
    assert hydrated.agent_seeds == {}
    assert hydrated.price_feeds.fragments == ()
    assert hydrated.start_slot == 42


def test_materialize_fork_raises_when_protocol_unregistered() -> None:
    initial = InitialState(slot=1)
    initial.merge(_pool("missing_protocol", "PoolA"))
    registry = _registry()

    with pytest.raises(LookupError):
        materialize_fork(
            initial,
            registry,
            parameters=ParameterStore(),
            numeric_mode=FLOAT_MODE,
        )


def test_hydrated_fork_is_frozen() -> None:
    initial = InitialState(slot=1)
    registry = _registry()
    hydrated = materialize_fork(
        initial, registry, parameters=ParameterStore(), numeric_mode=FLOAT_MODE
    )

    with pytest.raises(Exception):
        hydrated.start_slot = 99  # type: ignore[misc]


def test_price_feed_registry_from_fragments_preserves_order() -> None:
    frags = [_oracle("A", 1.0), _oracle("B", 2.0), _oracle("C", 3.0)]
    feeds = PriceFeedRegistry.from_fragments(frags)
    assert [f.pubkey for f in feeds.fragments] == ["A", "B", "C"]
