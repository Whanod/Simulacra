"""``ForkLoader`` contract tests (PRD US-003 line 483).

Exercises the loader against fake registry / hydrator / historical-backend
implementations so the test stays offline and does not depend on real
RPC fixtures or the corpus path. The same approach iter 106 used for the
``StateHydrator`` ABC.
"""

from __future__ import annotations

from typing import Any

import pytest

from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.state_hydrator import AccountFilter, OracleId, StateHydrator
from defi_sim_solana.replay import account_client as account_client_mod


@pytest.fixture(autouse=True)
def _reset_program_accounts_cache() -> None:
    account_client_mod.clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


class _FakeHydrator(StateHydrator):
    program_id = "FakeWhirl1111111111111111111111111111111111"
    schema_version = 7
    _disc = b"\x77" * 8

    def __init__(self, oracle_ids: tuple[OracleId, ...] = ()) -> None:
        self._oracle_ids = oracle_ids

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

    def oracle_dependencies(self) -> list[OracleId]:
        return list(self._oracle_ids)


class _FakeForkableMarket(ForkableMarket):
    state_hydrator = _FakeHydrator()

    @classmethod
    def from_initial_state(cls, fragments, *, parameters, numeric_mode):
        raise AssertionError(
            "ForkLoader must not call from_initial_state — "
            "materialize_fork (PRD line 543) owns that step."
        )


class _FakeBackend:
    """Minimal ``HistoricalAccountBackend`` returning canned program accounts."""

    endpoint = "fake://archive"

    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self._accounts = accounts
        self.calls: list[tuple[str, int, bytes | None]] = []

    def get_program_accounts_at_slot(
        self,
        program_id: str,
        slot: int,
        *,
        discriminator: bytes | None = None,
    ) -> dict[str, Any]:
        self.calls.append((program_id, slot, discriminator))
        return {
            "program_id": program_id,
            "slot": slot,
            "accounts": self._accounts,
        }


def _account(pubkey: str, raw_bytes: bytes) -> dict[str, Any]:
    import base64

    return {
        "pubkey": pubkey,
        "account": {
            "owner": "FakeWhirl1111111111111111111111111111111111",
            "lamports": 1,
            "data": [base64.b64encode(raw_bytes).decode("ascii"), "base64"],
        },
    }


def test_fork_loader_load_returns_initial_state_with_parsed_fragments() -> None:
    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01\x02"), _account("PoolB", b"\x03")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(slot=99, protocols=[ProtocolForkRequest(protocol_model="fakepool")])
    )

    assert isinstance(initial, InitialState)
    assert initial.slot == 99
    assert [f.pubkey for f in initial.fragments] == ["PoolA", "PoolB"]
    assert [f.payload["data_len"] for f in initial.fragments] == [2, 1]
    assert backend.calls == [
        (_FakeHydrator.program_id, 99, _FakeHydrator._disc)
    ], "discriminator from hydrator.account_filters() must thread to the backend"


def test_fork_loader_calls_correct_program_id_per_protocol() -> None:
    """A multi-protocol fork must call the historical backend with each
    hydrator's own ``program_id`` and discriminator, not collapse them.
    """

    class _AltHydrator(_FakeHydrator):
        program_id = "AltProg222222222222222222222222222222222222"
        schema_version = 3
        _disc = b"\xaa" * 8

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="pool",
                protocol_model="altpool",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    class _AltForkableMarket(ForkableMarket):
        state_hydrator = _AltHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    registry = ProtocolModelRegistry(
        {"fakepool": _FakeForkableMarket, "altpool": _AltForkableMarket}
    )
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    loader.load(
        ForkSpec(
            slot=55,
            protocols=[
                ProtocolForkRequest(protocol_model="fakepool"),
                ProtocolForkRequest(protocol_model="altpool"),
            ],
        )
    )

    assert backend.calls == [
        (_FakeHydrator.program_id, 55, _FakeHydrator._disc),
        (_AltHydrator.program_id, 55, _AltHydrator._disc),
    ]


def test_fork_loader_applies_account_filter() -> None:
    """The discriminator returned by ``hydrator.account_filters()[0]`` must
    thread through to ``backend.get_program_accounts_at_slot`` unchanged.

    Pinned in its own test (separate from
    ``test_fork_loader_load_returns_initial_state_with_parsed_fragments``)
    so a regression in filter routing fails with a single, focused diagnostic
    instead of being lost in a multi-assertion test. Uses a non-trivial
    discriminator (sha256-style 8 bytes) distinct from the default
    ``b"\\x77" * 8`` to make sure the filter content — not just any 8 bytes —
    survives the round trip.
    """

    class _FilterHydrator(_FakeHydrator):
        _disc = b"\x18\x1e\xc8\x28\x05\x1c\x04\x77"  # arbitrary non-uniform 8 bytes

    class _FilterMarket(ForkableMarket):
        state_hydrator = _FilterHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    registry = ProtocolModelRegistry({"fakepool": _FilterMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    loader.load(
        ForkSpec(
            slot=123,
            protocols=[ProtocolForkRequest(protocol_model="fakepool")],
        )
    )

    assert len(backend.calls) == 1
    program_id, slot, discriminator = backend.calls[0]
    assert slot == 123
    assert program_id == _FilterHydrator.program_id
    assert discriminator == _FilterHydrator._disc, (
        "backend received a different discriminator than the hydrator declared"
    )


def test_fork_loader_walks_multiple_hydrator_account_filters() -> None:
    class _DualFilterHydrator(_FakeHydrator):
        _disc_a = b"\x01" * 8
        _disc_b = b"\x02" * 8

        def account_filters(self) -> list[AccountFilter]:
            return [
                AccountFilter(discriminator=self._disc_a),
                AccountFilter(discriminator=self._disc_b),
            ]

    class _DualFilterMarket(ForkableMarket):
        state_hydrator = _DualFilterHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    registry = ProtocolModelRegistry({"fakepool": _DualFilterMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=124,
            protocols=[ProtocolForkRequest(protocol_model="fakepool")],
        )
    )

    assert backend.calls == [
        (_DualFilterHydrator.program_id, 124, _DualFilterHydrator._disc_a),
        (_DualFilterHydrator.program_id, 124, _DualFilterHydrator._disc_b),
    ]
    assert [f.pubkey for f in initial.fragments] == ["PoolA", "PoolA"]


def test_fork_loader_applies_pubkey_allowlist() -> None:
    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend(
        [
            _account("PoolA", b"\x01"),
            _account("PoolB", b"\x02"),
            _account("PoolC", b"\x03"),
        ]
    )
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=42,
            protocols=[
                ProtocolForkRequest(
                    protocol_model="fakepool",
                    account_pubkey_allowlist=["PoolA", "PoolC"],
                )
            ],
        )
    )

    assert [f.pubkey for f in initial.fragments] == ["PoolA", "PoolC"]


def test_fork_loader_lookup_failure_surfaces_protocol_name() -> None:
    registry = ProtocolModelRegistry()  # empty
    loader = ForkLoader(registry, historical_backend=_FakeBackend([]))

    with pytest.raises(LookupError, match="missing_proto"):
        loader.load(
            ForkSpec(
                slot=1,
                protocols=[ProtocolForkRequest(protocol_model="missing_proto")],
            )
        )


def test_fork_loader_pulls_oracle_dependencies() -> None:
    """Each ``oracle_id`` from ``hydrator.oracle_dependencies()`` is loaded and
    its fragments merged into the returned ``InitialState`` alongside the
    pool fragments.

    The production ``_load_oracle`` raises ``NotImplementedError`` until the
    Pyth/Switchboard decoder ships (PRD US-003 line 670). This test pins the
    *dispatch* contract — that the loader walks every declared oracle id
    once and merges its fragments — by overriding ``_load_oracle`` with a
    spy that returns a synthetic ``oracle_price`` fragment.
    """

    class _DualOracleHydrator(_FakeHydrator):
        def __init__(self) -> None:
            super().__init__(oracle_ids=("PythSOLUSD", "PythUSDCUSD"))

    class _DualOracleMarket(ForkableMarket):
        state_hydrator = _DualOracleHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    class _SpyLoader(ForkLoader):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.oracle_calls: list[tuple[str, int]] = []

        def _load_oracle(self, oracle_id, slot):  # type: ignore[override]
            self.oracle_calls.append((oracle_id, slot))
            return InitialStateFragment(
                kind="oracle_price",
                protocol_model="oracle",
                pubkey=oracle_id,
                owner=None,
                payload={"price": 1.0},
            )

    registry = ProtocolModelRegistry({"fakepool": _DualOracleMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = _SpyLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=77,
            protocols=[ProtocolForkRequest(protocol_model="fakepool")],
        )
    )

    assert loader.oracle_calls == [("PythSOLUSD", 77), ("PythUSDCUSD", 77)], (
        "loader must call _load_oracle once per declared oracle dependency, "
        "preserving order"
    )
    kinds = [f.kind for f in initial.fragments]
    assert kinds == ["pool", "oracle_price", "oracle_price"], (
        "pool fragments must precede oracle fragments in merge order"
    )
    assert [f.pubkey for f in initial.fragments] == [
        "PoolA",
        "PythSOLUSD",
        "PythUSDCUSD",
    ]


def test_fork_loader_merges_wallet_overlay() -> None:
    """``ForkSpec.include_wallet_accounts`` causes wallet fragments to be
    merged into the returned ``InitialState`` after the protocol fragments.

    The production ``_load_wallet_accounts`` raises ``NotImplementedError``
    until the SPL/position decoders ship (PRD US-003 line 672). This test
    pins the *dispatch* contract — that the loader walks the wallet pubkeys
    once and merges the resulting fragments at the end of the merge order
    (after pool and oracle fragments) — by overriding ``_load_wallet_accounts``
    with a spy that returns synthetic ``wallet_balance`` / ``wallet_position``
    fragments. Same pattern as
    ``test_fork_loader_pulls_oracle_dependencies``.
    """

    class _SpyLoader(ForkLoader):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.wallet_calls: list[tuple[tuple[str, ...], int]] = []

        def _load_wallet_accounts(self, pubkeys, slot):  # type: ignore[override]
            self.wallet_calls.append((tuple(pubkeys), slot))
            fragments: list[InitialStateFragment] = []
            for pk in pubkeys:
                fragments.append(
                    InitialStateFragment(
                        kind="wallet_balance",
                        protocol_model="spl_token",
                        pubkey=f"{pk}_ata",
                        owner=pk,
                        payload={"amount": 100},
                    )
                )
                fragments.append(
                    InitialStateFragment(
                        kind="wallet_position",
                        protocol_model="fakepool",
                        pubkey=f"{pk}_position",
                        owner=pk,
                        payload={"liquidity": 0},
                    )
                )
            return fragments

    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = _SpyLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=88,
            protocols=[ProtocolForkRequest(protocol_model="fakepool")],
            include_wallet_accounts=["WaLLetA", "WaLLetB"],
        )
    )

    assert loader.wallet_calls == [(("WaLLetA", "WaLLetB"), 88)], (
        "loader must call _load_wallet_accounts once with the full pubkey "
        "list and the fork slot"
    )
    kinds = [f.kind for f in initial.fragments]
    assert kinds == [
        "pool",
        "wallet_balance",
        "wallet_position",
        "wallet_balance",
        "wallet_position",
    ], "pool fragments must precede wallet fragments in merge order"
    assert [f.owner for f in initial.fragments[1:]] == [
        "WaLLetA",
        "WaLLetA",
        "WaLLetB",
        "WaLLetB",
    ], "wallet fragments must carry the owning wallet pubkey"


def test_fork_loader_oracle_dependency_without_decoder_raises() -> None:
    """An oracle-declaring hydrator must surface a clear error until decoders ship."""

    class _OracleHydrator(_FakeHydrator):
        def __init__(self) -> None:
            super().__init__(oracle_ids=("PythSOLUSD",))

    class _OracleMarket(ForkableMarket):
        state_hydrator = _OracleHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    registry = ProtocolModelRegistry({"fakepool": _OracleMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    with pytest.raises(NotImplementedError, match="PythSOLUSD"):
        loader.load(
            ForkSpec(
                slot=10,
                protocols=[ProtocolForkRequest(protocol_model="fakepool")],
            )
        )


def test_fork_loader_wallet_overlay_without_decoder_raises() -> None:
    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend([])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    with pytest.raises(NotImplementedError, match="wallet"):
        loader.load(
            ForkSpec(
                slot=1,
                protocols=[ProtocolForkRequest(protocol_model="fakepool")],
                include_wallet_accounts=["WaLLet1"],
            )
        )


def test_fork_loader_consumes_corpus_loader_when_present() -> None:
    """When a corpus fixture is available the historical backend is not called."""
    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend([])  # would be empty

    fixture = {
        "program_id": _FakeHydrator.program_id,
        "slot": 7,
        "accounts": [_account("FromFixture", _FakeHydrator._disc + b"\x09")],
    }

    def _loader(slot: int, kind: str, program_id: str | None = None):
        if kind == "program_accounts" and slot == 7:
            return fixture
        return None

    loader = ForkLoader(
        registry, historical_backend=backend, corpus_loader=_loader
    )

    initial = loader.load(
        ForkSpec(slot=7, protocols=[ProtocolForkRequest(protocol_model="fakepool")])
    )

    assert [f.pubkey for f in initial.fragments] == ["FromFixture"]
    assert backend.calls == [], "corpus path must not invoke the historical backend"


def test_fork_loader_second_load_with_same_spec_hits_cache() -> None:
    """PRD US-003 line 660: re-running the same ``ForkSpec`` must not issue
    a second archival RPC call.

    The raw program-accounts wrapper (``get_program_accounts_at_slot``) is
    LRU-cached per (backend, corpus_root, program_id, slot, discriminator),
    so a second ``loader.load(spec)`` for the same spec must reuse the cached
    snapshot. Pinning the contract here means a refactor that drops or
    keys-the-wrong-way the cache fails fast with one focused diagnostic.
    """
    registry = ProtocolModelRegistry({"fakepool": _FakeForkableMarket})
    backend = _FakeBackend([_account("PoolA", b"\x01"), _account("PoolB", b"\x02")])
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )
    spec = ForkSpec(
        slot=314,
        protocols=[ProtocolForkRequest(protocol_model="fakepool")],
    )

    first = loader.load(spec)
    assert len(backend.calls) == 1, "first load must hit the backend exactly once"

    second = loader.load(spec)
    assert len(backend.calls) == 1, (
        "second load with same ForkSpec must not issue another archival RPC; "
        f"observed {len(backend.calls)} backend calls"
    )
    assert second is first, "parsed InitialState should be served from ForkLoader cache"
    assert [f.pubkey for f in second.fragments] == [f.pubkey for f in first.fragments]


def test_fork_state_size_is_linear_in_protocols_times_accounts_per_protocol() -> None:
    """PRD US-003 line 662: total fork state size is
    ``O(protocols × accounts_per_protocol)``, not ``O(all of mainnet)``.

    Pins the no-leakage contract: each protocol's hydrator only sees the
    accounts owned by its own ``program_id``, so the total fragment count
    equals ``sum(accounts_per_protocol)``. A regression that, e.g., piped
    every protocol's snapshot through every hydrator (``N × N × M``) or
    forgot the discriminator/program-id key (``N × M_total``) would fail
    this test loudly.
    """

    class _MultiProgramBackend:
        endpoint = "fake://archive"

        def __init__(self, accounts_by_program: dict[str, list[dict[str, Any]]]) -> None:
            self._accounts_by_program = accounts_by_program
            self.calls: list[tuple[str, int, bytes | None]] = []

        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            self.calls.append((program_id, slot, discriminator))
            return {
                "program_id": program_id,
                "slot": slot,
                "accounts": self._accounts_by_program.get(program_id, []),
            }

    def _make_protocol(model_name: str, program_id_seed: str, disc_byte: int):
        class _H(_FakeHydrator):
            program_id = program_id_seed
            schema_version = 1
            _disc = bytes([disc_byte]) * 8

            def parse_account(self, pubkey, data):  # type: ignore[override]
                return InitialStateFragment(
                    kind="pool",
                    protocol_model=model_name,
                    pubkey=pubkey,
                    owner=None,
                    payload={"data_len": len(data)},
                )

        class _M(ForkableMarket):
            state_hydrator = _H()

            @classmethod
            def from_initial_state(cls, fragments, *, parameters, numeric_mode):
                raise AssertionError("not called")

        return model_name, _M, _H

    proto_a = _make_protocol("alphapool", "AlphaProg1111111111111111111111111111111111", 0xA1)
    proto_b = _make_protocol("betapool", "BetaProg22222222222222222222222222222222222", 0xB2)
    proto_c = _make_protocol("gammapool", "GammaProg3333333333333333333333333333333333", 0xC3)

    counts = {proto_a[0]: 2, proto_b[0]: 3, proto_c[0]: 5}
    accounts_by_program: dict[str, list[dict[str, Any]]] = {}
    for model_name, _market_cls, hydrator_cls in (proto_a, proto_b, proto_c):
        accounts_by_program[hydrator_cls.program_id] = [
            _account(f"{model_name}_pool_{i}", bytes([i + 1]))
            for i in range(counts[model_name])
        ]

    registry = ProtocolModelRegistry(
        {name: market_cls for (name, market_cls, _h) in (proto_a, proto_b, proto_c)}
    )
    backend = _MultiProgramBackend(accounts_by_program)
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=4242,
            protocols=[
                ProtocolForkRequest(protocol_model=proto_a[0]),
                ProtocolForkRequest(protocol_model=proto_b[0]),
                ProtocolForkRequest(protocol_model=proto_c[0]),
            ],
        )
    )

    expected_total = sum(counts.values())  # 2 + 3 + 5 = 10
    assert len(initial.fragments) == expected_total, (
        f"total fragments must equal sum of accounts per protocol "
        f"({expected_total}); observed {len(initial.fragments)} (leakage / "
        f"cross-protocol parsing would yield more)"
    )

    by_model: dict[str, int] = {}
    for frag in initial.fragments:
        by_model[frag.protocol_model] = by_model.get(frag.protocol_model, 0) + 1
    assert by_model == counts, (
        f"per-protocol fragment counts must match input account counts; "
        f"expected {counts}, observed {by_model}"
    )

    # Each hydrator's program_id must be queried exactly once — no quadratic
    # blow-up across protocols.
    queried_program_ids = [program_id for program_id, _slot, _disc in backend.calls]
    assert len(queried_program_ids) == 3
    assert set(queried_program_ids) == {
        proto_a[2].program_id,
        proto_b[2].program_id,
        proto_c[2].program_id,
    }


def test_fork_with_only_whirlpool_excludes_other_program_accounts() -> None:
    """PRD US-003 line 658: ``Fork(slot=N, protocols=[Whirlpool])`` produces an
    initial state containing every Whirlpool pool account at that slot — and
    *nothing else* (no Raydium, no MarginFi).

    The sibling test ``test_fork_state_size_is_linear_...`` pins the
    no-leakage property when the caller asks for *all* protocols. This test
    pins the complementary property: when the caller asks for *only* one
    protocol, accounts owned by sibling programs that the backend happens to
    serve must not appear in the resulting ``InitialState`` — even though they
    sit alongside the selected program's accounts on mainnet at the same slot.

    Uses the real :data:`WHIRLPOOL_PROGRAM` constant (``whirLb...``) and stand-
    in pubkeys for Raydium / MarginFi so the diagnostic names the actual
    program ids the bullet calls out — a regression that, e.g., bypassed the
    discriminator routing and pulled accounts from every program in the
    backend would surface a Raydium or MarginFi pubkey in the output and
    fail with that name.
    """
    from defi_sim_solana.replay.whirlpool_hydrator import WHIRLPOOL_PROGRAM

    raydium_program = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
    marginfi_program = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"

    class _MultiProgramBackend:
        endpoint = "fake://archive"

        def __init__(self, accounts_by_program: dict[str, list[dict[str, Any]]]) -> None:
            self._accounts_by_program = accounts_by_program
            self.calls: list[tuple[str, int, bytes | None]] = []

        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            self.calls.append((program_id, slot, discriminator))
            return {
                "program_id": program_id,
                "slot": slot,
                "accounts": self._accounts_by_program.get(program_id, []),
            }

    class _WhirlpoolFakeHydrator(_FakeHydrator):
        program_id = WHIRLPOOL_PROGRAM
        schema_version = 1
        _disc = b"\x3f\x83\x7c\xc2\xc1\x6e\x5c\x95"  # Whirlpool pool discriminator (arbitrary 8 bytes here)

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="pool",
                protocol_model="whirlpool",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    class _WhirlpoolFakeMarket(ForkableMarket):
        state_hydrator = _WhirlpoolFakeHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    accounts_by_program = {
        WHIRLPOOL_PROGRAM: [
            _account("WhirlPoolA", b"\x01"),
            _account("WhirlPoolB", b"\x02"),
            _account("WhirlPoolC", b"\x03"),
        ],
        # Sibling program accounts that MUST NOT appear in the output. If the
        # loader regressed to a wildcard walk, these would leak through.
        raydium_program: [
            _account("RaydiumPool1", b"\x10"),
            _account("RaydiumPool2", b"\x11"),
        ],
        marginfi_program: [
            _account("MarginFiBank1", b"\x20"),
        ],
    }

    registry = ProtocolModelRegistry({"whirlpool": _WhirlpoolFakeMarket})
    backend = _MultiProgramBackend(accounts_by_program)
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=420_196_842,
            protocols=[ProtocolForkRequest(protocol_model="whirlpool")],
        )
    )

    pubkeys = [f.pubkey for f in initial.fragments]
    assert pubkeys == ["WhirlPoolA", "WhirlPoolB", "WhirlPoolC"], (
        f"fork(protocols=[Whirlpool]) must yield exactly the Whirlpool pool "
        f"accounts at slot 420_196_842 in backend order; observed {pubkeys}"
    )
    protocol_models = {f.protocol_model for f in initial.fragments}
    assert protocol_models == {"whirlpool"}, (
        f"every fragment must be tagged protocol_model='whirlpool'; observed "
        f"{protocol_models} (Raydium / MarginFi leakage)"
    )
    queried_program_ids = [program_id for program_id, _slot, _disc in backend.calls]
    assert queried_program_ids == [WHIRLPOOL_PROGRAM], (
        f"backend must be queried with the Whirlpool program id only — "
        f"sibling programs (Raydium {raydium_program!r}, MarginFi "
        f"{marginfi_program!r}) must not be touched; observed {queried_program_ids}"
    )


def test_fork_compose_whirlpool_marginfi_pythpullsol_includes_oracle_no_leakage() -> None:
    """PRD US-003 line 659: ``Fork(slot=420_196_842, protocols=[Whirlpool,
    MarginFi, PythPullSOL])`` composes 3 protocols, oracle account included,
    no other accounts.

    Complement to ``test_fork_with_only_whirlpool_excludes_other_program_accounts``
    (line 658, single-protocol no-leakage). This test pins the *multi-protocol*
    composition contract:

    1. **Three protocols compose.** Whirlpool (CLMM pool), MarginFi (lending
       reserve), and PythPullSOL (oracle) are each loaded via their own
       hydrator and their fragments interleave in the resulting
       ``InitialState`` in protocol-list order.
    2. **Oracle account included.** The PythPullSOL ``oracle_price`` fragment
       appears in the output — composition does NOT silently drop oracle-kind
       protocols.
    3. **No other accounts.** Sibling-program accounts that the historical
       backend happens to serve at the same slot (Raydium AMM v4 in this
       fixture) MUST NOT appear in the ``InitialState``. A regression that
       walked every account in the backend would surface a ``RaydiumPool*``
       pubkey and fail with that name.

    Uses real mainnet program ids for diagnostic clarity (a leakage failure
    names the protocol by program id, not an opaque ``fakeprog4``).
    """
    from defi_sim_solana.replay.whirlpool_hydrator import WHIRLPOOL_PROGRAM

    marginfi_program = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"
    # Pyth Solana Receiver (the Pyth Pull oracle program on Solana mainnet).
    pyth_pull_program = "rec5EKMGg6MxZYaMdyBfgwp4d5rB9T1VQH5pJv5LtFJ"
    # Sibling program that MUST NOT leak into the InitialState.
    raydium_program = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

    class _MultiProgramBackend:
        endpoint = "fake://archive"

        def __init__(self, accounts_by_program: dict[str, list[dict[str, Any]]]) -> None:
            self._accounts_by_program = accounts_by_program
            self.calls: list[tuple[str, int, bytes | None]] = []

        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            self.calls.append((program_id, slot, discriminator))
            return {
                "program_id": program_id,
                "slot": slot,
                "accounts": self._accounts_by_program.get(program_id, []),
            }

    class _WhirlpoolFakeHydrator(_FakeHydrator):
        program_id = WHIRLPOOL_PROGRAM
        schema_version = 1
        _disc = b"\x3f\x83\x7c\xc2\xc1\x6e\x5c\x95"

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="pool",
                protocol_model="whirlpool",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    class _MarginFiFakeHydrator(_FakeHydrator):
        program_id = marginfi_program
        schema_version = 1
        _disc = b"\x47\x52\x9d\x66\xb2\x21\xa1\xea"

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="lending_reserve",
                protocol_model="marginfi",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    class _PythPullSOLFakeHydrator(_FakeHydrator):
        program_id = pyth_pull_program
        schema_version = 1
        _disc = b"\x66\x9b\x4d\xc2\x29\x6e\xf0\x21"

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="oracle_price",
                protocol_model="pyth_pull_sol",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    def _make_market(hydrator_instance):
        class _M(ForkableMarket):
            state_hydrator = hydrator_instance

            @classmethod
            def from_initial_state(cls, fragments, *, parameters, numeric_mode):
                raise AssertionError("not called")

        return _M

    accounts_by_program = {
        WHIRLPOOL_PROGRAM: [
            _account("WhirlPoolA", b"\x01"),
            _account("WhirlPoolB", b"\x02"),
        ],
        marginfi_program: [
            _account("MarginFiBank1", b"\x10"),
            _account("MarginFiBank2", b"\x11"),
            _account("MarginFiBank3", b"\x12"),
        ],
        pyth_pull_program: [
            _account("PythSOLUSDFeed", b"\x20\x21"),
        ],
        # Sibling program — accounts MUST NOT appear in the InitialState.
        raydium_program: [
            _account("RaydiumPool1", b"\x30"),
            _account("RaydiumPool2", b"\x31"),
        ],
    }

    registry = ProtocolModelRegistry(
        {
            "whirlpool": _make_market(_WhirlpoolFakeHydrator()),
            "marginfi": _make_market(_MarginFiFakeHydrator()),
            "pyth_pull_sol": _make_market(_PythPullSOLFakeHydrator()),
        }
    )
    backend = _MultiProgramBackend(accounts_by_program)
    loader = ForkLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    initial = loader.load(
        ForkSpec(
            slot=420_196_842,
            protocols=[
                ProtocolForkRequest(protocol_model="whirlpool"),
                ProtocolForkRequest(protocol_model="marginfi"),
                ProtocolForkRequest(protocol_model="pyth_pull_sol"),
            ],
        )
    )

    # 1. Three protocols compose — every fragment is tagged with one of the
    #    three requested protocol models, in protocol-list order.
    protocol_models = [f.protocol_model for f in initial.fragments]
    assert set(protocol_models) == {"whirlpool", "marginfi", "pyth_pull_sol"}, (
        f"composition must include all three requested protocols; observed "
        f"protocol_models={set(protocol_models)}"
    )
    assert protocol_models == [
        "whirlpool",
        "whirlpool",
        "marginfi",
        "marginfi",
        "marginfi",
        "pyth_pull_sol",
    ], (
        f"fragments must appear in protocol-list order (Whirlpool then "
        f"MarginFi then PythPullSOL); observed {protocol_models}"
    )

    # 2. Oracle account is included in the output.
    oracle_fragments = [f for f in initial.fragments if f.kind == "oracle_price"]
    assert len(oracle_fragments) == 1, (
        f"PythPullSOL oracle_price fragment must appear in the InitialState; "
        f"observed {len(oracle_fragments)} oracle_price fragments"
    )
    assert oracle_fragments[0].pubkey == "PythSOLUSDFeed"
    assert oracle_fragments[0].protocol_model == "pyth_pull_sol"

    # 3. No other accounts — Raydium is in the backend but must not leak.
    pubkeys = [f.pubkey for f in initial.fragments]
    assert pubkeys == [
        "WhirlPoolA",
        "WhirlPoolB",
        "MarginFiBank1",
        "MarginFiBank2",
        "MarginFiBank3",
        "PythSOLUSDFeed",
    ], (
        f"only requested protocols' accounts may appear; observed {pubkeys} "
        f"(Raydium leakage would surface a 'RaydiumPool*' pubkey)"
    )

    # Backend was queried for exactly the three requested program ids — never
    # for the sibling Raydium program.
    queried_program_ids = [program_id for program_id, _slot, _disc in backend.calls]
    assert queried_program_ids == [
        WHIRLPOOL_PROGRAM,
        marginfi_program,
        pyth_pull_program,
    ], (
        f"backend must be queried with each of the 3 requested program ids "
        f"exactly once, in protocol-list order; sibling programs (Raydium "
        f"{raydium_program!r}) must not be touched; observed {queried_program_ids}"
    )


def test_fork_marginfi_with_wallet_overlay_yields_marginfi_state_plus_user_accounts() -> None:
    """PRD US-003 line 661: ``Fork(slot=N, protocols=[MarginFi],
    include_wallet_accounts=[my_wallet])`` produces MarginFi state plus the
    user's accounts.

    Sibling to the no-leakage validation bullets:

    * line 658 (single-protocol no-leakage) — pinned by
      ``test_fork_with_only_whirlpool_excludes_other_program_accounts``
    * line 659 (composition no-leakage) — pinned by
      ``test_fork_compose_whirlpool_marginfi_pythpullsol_includes_oracle_no_leakage``
    * line 662 (linear-size) — pinned by
      ``test_fork_state_size_is_linear_in_protocols_times_accounts_per_protocol``

    This test pins the **wallet-overlay** addition to that no-leakage matrix:
    when the caller asks for ``protocols=[MarginFi]`` + a wallet overlay, the
    output ``InitialState`` must contain exactly:

    1. MarginFi protocol fragments (and only MarginFi — no Whirlpool, no
       Raydium accounts that the historical backend serves at the same slot).
    2. Wallet-overlay fragments tagged with the requested wallet pubkey,
       appended after the protocol fragments (merge-order contract from
       ``test_fork_loader_merges_wallet_overlay``).

    The production ``_load_wallet_accounts`` is still
    ``NotImplementedError`` until the SPL/position decoders ship (PRD US-003
    line 672 / US-009). This test pins the *loader-level* dispatch +
    no-leakage contract using the same spy-loader pattern as
    ``test_fork_loader_merges_wallet_overlay`` so the wallet-overlay leakage
    contract is locked in *before* the decoders land — a regression that
    e.g. piped wallet pubkeys through the protocol parser, or mixed wallet
    accounts into the protocol query path, would fail this test loudly.

    Uses real mainnet program ids so leakage failures name the actual
    program by id rather than an opaque ``fakeprog``.
    """
    marginfi_program = "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA"
    # Sibling programs that MUST NOT leak into the InitialState.
    from defi_sim_solana.replay.whirlpool_hydrator import WHIRLPOOL_PROGRAM
    raydium_program = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

    class _MultiProgramBackend:
        endpoint = "fake://archive"

        def __init__(self, accounts_by_program: dict[str, list[dict[str, Any]]]) -> None:
            self._accounts_by_program = accounts_by_program
            self.calls: list[tuple[str, int, bytes | None]] = []

        def get_program_accounts_at_slot(
            self,
            program_id: str,
            slot: int,
            *,
            discriminator: bytes | None = None,
        ) -> dict[str, Any]:
            self.calls.append((program_id, slot, discriminator))
            return {
                "program_id": program_id,
                "slot": slot,
                "accounts": self._accounts_by_program.get(program_id, []),
            }

    class _MarginFiFakeHydrator(_FakeHydrator):
        program_id = marginfi_program
        schema_version = 1
        _disc = b"\x47\x52\x9d\x66\xb2\x21\xa1\xea"

        def parse_account(self, pubkey, data):  # type: ignore[override]
            return InitialStateFragment(
                kind="lending_reserve",
                protocol_model="marginfi",
                pubkey=pubkey,
                owner=None,
                payload={"data_len": len(data)},
            )

    class _MarginFiFakeMarket(ForkableMarket):
        state_hydrator = _MarginFiFakeHydrator()

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not called")

    class _SpyLoader(ForkLoader):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.wallet_calls: list[tuple[tuple[str, ...], int]] = []

        def _load_wallet_accounts(self, pubkeys, slot):  # type: ignore[override]
            self.wallet_calls.append((tuple(pubkeys), slot))
            fragments: list[InitialStateFragment] = []
            for pk in pubkeys:
                # SPL token (USDC ATA the user holds) — non-protocol balance.
                fragments.append(
                    InitialStateFragment(
                        kind="wallet_balance",
                        protocol_model="spl_token",
                        pubkey=f"{pk}_usdc_ata",
                        owner=pk,
                        payload={"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "amount": 1_000_000},
                    )
                )
                # MarginFi user account (the per-protocol position the wallet holds).
                fragments.append(
                    InitialStateFragment(
                        kind="wallet_position",
                        protocol_model="marginfi",
                        pubkey=f"{pk}_marginfi_account",
                        owner=pk,
                        payload={"deposited_shares": 250},
                    )
                )
            return fragments

    accounts_by_program = {
        marginfi_program: [
            _account("MarginFiBank_SOL", b"\x10"),
            _account("MarginFiBank_USDC", b"\x11"),
        ],
        # Sibling programs — MUST NOT appear in the InitialState. If the
        # loader regressed to a wildcard walk OR if wallet overlay leaked
        # through the protocol query path, these would leak through.
        WHIRLPOOL_PROGRAM: [
            _account("WhirlPool_SOLUSDC", b"\x20"),
        ],
        raydium_program: [
            _account("RaydiumPool_SOLUSDC", b"\x30"),
        ],
    }

    registry = ProtocolModelRegistry({"marginfi": _MarginFiFakeMarket})
    backend = _MultiProgramBackend(accounts_by_program)
    loader = _SpyLoader(
        registry,
        historical_backend=backend,
        corpus_loader=lambda *_a, **_kw: None,
    )

    my_wallet = "MyWaLLetXYZ1111111111111111111111111111111"
    initial = loader.load(
        ForkSpec(
            slot=420_196_842,
            protocols=[ProtocolForkRequest(protocol_model="marginfi")],
            include_wallet_accounts=[my_wallet],
        )
    )

    # 1. MarginFi state present, in backend order.
    marginfi_pubkeys = [f.pubkey for f in initial.fragments if f.protocol_model == "marginfi" and f.kind == "lending_reserve"]
    assert marginfi_pubkeys == ["MarginFiBank_SOL", "MarginFiBank_USDC"], (
        f"MarginFi lending_reserve fragments must be present in backend order; "
        f"observed {marginfi_pubkeys}"
    )

    # 2. Wallet overlay merged at the end, tagged with the requested wallet.
    wallet_fragments = [f for f in initial.fragments if f.kind in ("wallet_balance", "wallet_position")]
    assert len(wallet_fragments) == 2, (
        f"wallet overlay must produce exactly 2 fragments for one wallet "
        f"(SPL balance + MarginFi position); observed {len(wallet_fragments)}"
    )
    assert all(f.owner == my_wallet for f in wallet_fragments), (
        f"every wallet fragment must carry owner={my_wallet!r}; observed "
        f"{[f.owner for f in wallet_fragments]}"
    )

    # 3. Merge order: MarginFi protocol fragments first, then wallet overlay.
    kinds = [f.kind for f in initial.fragments]
    assert kinds == [
        "lending_reserve",
        "lending_reserve",
        "wallet_balance",
        "wallet_position",
    ], (
        f"protocol fragments must precede wallet fragments in merge order; "
        f"observed kinds={kinds}"
    )

    # 4. No leakage — the InitialState contains exactly MarginFi + wallet,
    #    nothing else. Sibling Whirlpool/Raydium pubkeys must not surface.
    pubkeys = [f.pubkey for f in initial.fragments]
    assert pubkeys == [
        "MarginFiBank_SOL",
        "MarginFiBank_USDC",
        f"{my_wallet}_usdc_ata",
        f"{my_wallet}_marginfi_account",
    ], (
        f"only MarginFi state + the user's wallet accounts may appear; "
        f"observed {pubkeys} (Whirlpool / Raydium leakage would surface a "
        f"'WhirlPool_*' or 'RaydiumPool_*' pubkey)"
    )

    # 5. Backend was queried for MarginFi only — wallet overlay must NOT
    #    fan out to the historical backend's program-accounts path. Sibling
    #    programs (Whirlpool, Raydium) must never be touched.
    queried_program_ids = [program_id for program_id, _slot, _disc in backend.calls]
    assert queried_program_ids == [marginfi_program], (
        f"backend must be queried with the MarginFi program id only; sibling "
        f"programs (Whirlpool {WHIRLPOOL_PROGRAM!r}, Raydium "
        f"{raydium_program!r}) must not be touched, and the wallet overlay "
        f"must not fan out program-accounts queries; observed {queried_program_ids}"
    )

    # 6. Wallet-overlay dispatch contract: _load_wallet_accounts called once
    #    with the requested pubkey list and the fork slot.
    assert loader.wallet_calls == [((my_wallet,), 420_196_842)], (
        f"_load_wallet_accounts must be called once with the full pubkey "
        f"list and the fork slot; observed {loader.wallet_calls}"
    )


def test_protocol_model_registry_register_and_lookup() -> None:
    registry = ProtocolModelRegistry()
    registry.register("fakepool", _FakeForkableMarket)
    assert registry.lookup("fakepool") is _FakeForkableMarket


def test_fork_loader_module_docstring_documents_explicit_non_goals() -> None:
    """PRD US-003 line 650: non-goals must be visible in the module docstring."""
    from defi_sim.engine import fork_hydration, fork_loader

    for module in (fork_loader, fork_hydration):
        doc = module.__doc__ or ""
        assert "non-goals" in doc.lower(), (
            f"{module.__name__} docstring missing explicit non-goals section"
        )
        assert "sysvar" in doc.lower()
        assert "unrelated programs" in doc.lower()
        assert "full-account-index" in doc.lower()
        assert "ledger replay" in doc.lower()
