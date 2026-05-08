"""``cache_key`` + ``InitialStateCache`` contract tests (PRD US-003 line 526).

The cache key participates in invalidation: bumping a hydrator's
``schema_version`` must rotate the digest. These tests pin the four
participating axes (slot, protocol set, allowlist, schema_version) so a
later refactor cannot silently start serving stale parsed state.
"""

from __future__ import annotations

import pytest

from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_cache import InitialStateCache, cache_key
from defi_sim.engine.fork_loader import ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator


def _make_hydrator(version: int):
    class _H(StateHydrator):
        program_id = "FakeProg11111111111111111111111111111111111"
        schema_version = version

        def account_filters(self) -> list[AccountFilter]:
            return []

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="pool",
                protocol_model="fakepool",
                pubkey=pubkey,
                owner=None,
                payload={},
            )

    return _H()


def _make_market(hydrator: StateHydrator) -> type[ForkableMarket]:
    class _M(ForkableMarket):
        state_hydrator = hydrator

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not used in cache tests")

    return _M


def _registry(models: dict[str, type[ForkableMarket]]) -> ProtocolModelRegistry:
    return ProtocolModelRegistry(models=models)


def test_cache_key_is_deterministic_hex_digest() -> None:
    registry = _registry({"fakepool": _make_market(_make_hydrator(1))})
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    key1 = cache_key(spec, registry)
    key2 = cache_key(spec, registry)
    assert key1 == key2
    assert len(key1) == 64
    int(key1, 16)  # all hex


def test_fork_spec_hash_is_deterministic() -> None:
    """PRD US-003 line 673: independently constructed ``ForkSpec`` instances
    with identical content must produce the same cache key.

    ``test_cache_key_is_deterministic_hex_digest`` reuses one ``spec`` object;
    that only proves memoization. This pins the stronger property that the
    hash depends purely on serialized content — two fresh objects with the
    same fields hash identically. Required so two independent process runs
    (or two independent code paths producing the same logical fork) hit the
    same on-disk artifact.
    """
    r1 = _registry({"fakepool": _make_market(_make_hydrator(1))})
    r2 = _registry({"fakepool": _make_market(_make_hydrator(1))})
    spec_a = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["A", "B"])
        ],
        include_wallet_accounts=["w1"],
    )
    spec_b = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["A", "B"])
        ],
        include_wallet_accounts=["w1"],
    )
    assert spec_a is not spec_b
    assert cache_key(spec_a, r1) == cache_key(spec_b, r2)


def test_cache_key_differs_for_different_slots() -> None:
    registry = _registry({"fakepool": _make_market(_make_hydrator(1))})
    a = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    b = ForkSpec(slot=43, protocols=[ProtocolForkRequest("fakepool")])
    assert cache_key(a, registry) != cache_key(b, registry)


def test_cache_key_changes_when_hydrator_schema_version_bumps() -> None:
    """PRD US-003 line 675: same ``ForkSpec``, increment hydrator's
    ``schema_version``; cache key must differ from before.

    A parser-bug fix that bumps ``schema_version`` must invalidate the cache.
    """
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    r1 = _registry({"fakepool": _make_market(_make_hydrator(1))})
    r2 = _registry({"fakepool": _make_market(_make_hydrator(2))})
    assert cache_key(spec, r1) != cache_key(spec, r2)


def test_cache_key_differs_when_protocol_order_swaps() -> None:
    """Protocol order drives materializer iteration order (PRD line 543/563)."""
    registry = _registry(
        {
            "fakepool": _make_market(_make_hydrator(1)),
            "otherpool": _make_market(_make_hydrator(1)),
        }
    )
    a = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("fakepool"),
            ProtocolForkRequest("otherpool"),
        ],
    )
    b = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("otherpool"),
            ProtocolForkRequest("fakepool"),
        ],
    )
    assert cache_key(a, registry) != cache_key(b, registry)


def test_cache_key_differs_for_different_pubkey_allowlist() -> None:
    registry = _registry({"fakepool": _make_market(_make_hydrator(1))})
    a = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["A"])
        ],
    )
    b = ForkSpec(
        slot=42,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["B"])
        ],
    )
    assert cache_key(a, registry) != cache_key(b, registry)


def test_cache_key_differs_when_wallet_overlay_added() -> None:
    registry = _registry({"fakepool": _make_market(_make_hydrator(1))})
    base = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    overlay = ForkSpec(
        slot=42,
        protocols=[ProtocolForkRequest("fakepool")],
        include_wallet_accounts=["mywallet"],
    )
    assert cache_key(base, registry) != cache_key(overlay, registry)


def test_cache_key_raises_when_protocol_unregistered() -> None:
    registry = _registry({})
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("missing")])
    with pytest.raises(LookupError):
        cache_key(spec, registry)


def test_initial_state_cache_get_put_roundtrip() -> None:
    cache = InitialStateCache()
    state = InitialState(slot=99)
    assert cache.get("k") is None
    cache.put("k", state)
    assert cache.get("k") is state
    assert "k" in cache
    assert len(cache) == 1


def test_fork_spec_cache_key_round_trips() -> None:
    """PRD US-003 line 674: ``cache_key(spec, registry)`` is the canonical
    lookup index for ``InitialStateCache``.

    Composes the two contracts already covered separately (``cache_key`` is
    deterministic across equal-content specs; cache ``put``/``get`` round-trips
    by string key) into the end-to-end claim: a second, independently
    constructed ``ForkSpec`` with identical content must retrieve the
    ``InitialState`` that the first put stored. This is the property the
    on-disk fork-artifact path actually relies on — two processes producing
    the same logical fork must hit the same cache slot.
    """
    r1 = _registry({"fakepool": _make_market(_make_hydrator(1))})
    r2 = _registry({"fakepool": _make_market(_make_hydrator(1))})
    spec_a = ForkSpec(
        slot=250_000_000,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["A", "B"])
        ],
        include_wallet_accounts=["w1"],
    )
    spec_b = ForkSpec(
        slot=250_000_000,
        protocols=[
            ProtocolForkRequest("fakepool", account_pubkey_allowlist=["A", "B"])
        ],
        include_wallet_accounts=["w1"],
    )
    cache = InitialStateCache()
    state = InitialState(slot=250_000_000)
    cache.put(cache_key(spec_a, r1), state)
    assert cache.get(cache_key(spec_b, r2)) is state


def test_initial_state_cache_overwrites_on_repeat_put() -> None:
    cache = InitialStateCache()
    s1 = InitialState(slot=1)
    s2 = InitialState(slot=2)
    cache.put("k", s1)
    cache.put("k", s2)
    assert cache.get("k") is s2
    assert len(cache) == 1


def test_initial_state_cache_clear_empties() -> None:
    cache = InitialStateCache()
    cache.put("a", InitialState(slot=1))
    cache.put("b", InitialState(slot=2))
    cache.clear()
    assert len(cache) == 0
    assert cache.get("a") is None
