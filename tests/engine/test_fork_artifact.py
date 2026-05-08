"""Persisted fork-initial-state artifact tests (PRD US-003 line 642).

The on-disk format is the user-visible contract for "save this fork as a
reusable starting state" — a saved artifact must round-trip exactly back
to the same :class:`InitialState` regardless of whether it was small enough
to store as plain JSON or large enough to land as gzip.
"""

from __future__ import annotations

import gzip
import json

import pytest

from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.fork_artifact import (
    FORK_ARTIFACT_SCHEMA,
    ForkArtifactError,
    load_fork_initial_state,
    save_fork_initial_state,
)
from defi_sim.engine.fork_cache import cache_key
from defi_sim.engine.fork_loader import ProtocolModelRegistry
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator


def _make_hydrator(version: int = 1):
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


def _make_registry() -> ProtocolModelRegistry:
    hydrator = _make_hydrator()

    class _M(ForkableMarket):
        state_hydrator = hydrator

        @classmethod
        def from_initial_state(cls, fragments, *, parameters, numeric_mode):
            raise AssertionError("not used in artifact tests")

    return ProtocolModelRegistry(models={"fakepool": _M})


def _sample_initial_state() -> InitialState:
    return InitialState(
        slot=42,
        fragments=[
            InitialStateFragment(
                kind="pool",
                protocol_model="fakepool",
                pubkey="poolA",
                owner=None,
                payload={"reserve_a": 100, "reserve_b": 200},
            ),
            InitialStateFragment(
                kind="oracle_price",
                protocol_model="pyth_pull",
                pubkey="oracle1",
                owner=None,
                payload={"price": "1.23"},
            ),
            InitialStateFragment(
                kind="wallet_balance",
                protocol_model="spl_token",
                pubkey="acctA",
                owner="walletA",
                payload={"amount": 7},
            ),
        ],
    )


def test_save_writes_under_forks_subdir_named_by_cache_key(tmp_path) -> None:
    registry = _make_registry()
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    initial = _sample_initial_state()

    out = save_fork_initial_state(initial, spec, registry, tmp_path)

    expected_key = cache_key(spec, registry)
    assert out.parent == tmp_path / "forks"
    assert out.stem.startswith(expected_key) or out.name.startswith(expected_key)
    assert expected_key in out.name


def test_save_then_load_round_trip_preserves_fragments(tmp_path) -> None:
    registry = _make_registry()
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    initial = _sample_initial_state()

    out = save_fork_initial_state(initial, spec, registry, tmp_path)
    loaded = load_fork_initial_state(out)

    assert loaded.slot == initial.slot
    assert loaded.fragments == initial.fragments


def test_save_uses_plain_json_below_gzip_threshold(tmp_path) -> None:
    registry = _make_registry()
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    initial = _sample_initial_state()

    out = save_fork_initial_state(
        initial, spec, registry, tmp_path, gzip_threshold_bytes=1_000_000
    )

    assert out.suffix == ".json"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == FORK_ARTIFACT_SCHEMA


def test_save_uses_gzip_above_threshold(tmp_path) -> None:
    registry = _make_registry()
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    initial = _sample_initial_state()

    out = save_fork_initial_state(
        initial, spec, registry, tmp_path, gzip_threshold_bytes=0
    )

    assert out.suffixes[-2:] == [".json", ".gz"]
    with gzip.open(out, "rb") as fh:
        decoded = json.loads(fh.read().decode("utf-8"))
    assert decoded["schema"] == FORK_ARTIFACT_SCHEMA
    assert decoded["slot"] == 42


def test_load_rejects_unknown_schema(tmp_path) -> None:
    forks = tmp_path / "forks"
    forks.mkdir()
    bad = forks / "deadbeef.json"
    bad.write_text(
        json.dumps({"schema": "fork_initial_state.v999", "slot": 1, "fragments": []}),
        encoding="utf-8",
    )

    with pytest.raises(ForkArtifactError):
        load_fork_initial_state(bad)


def test_save_is_deterministic_for_same_inputs(tmp_path) -> None:
    """Same fork spec + state -> same bytes on disk (sorted keys, no clock noise)."""
    registry = _make_registry()
    spec = ForkSpec(slot=42, protocols=[ProtocolForkRequest("fakepool")])
    initial = _sample_initial_state()

    a = save_fork_initial_state(
        initial, spec, registry, tmp_path / "a", gzip_threshold_bytes=1_000_000
    )
    b = save_fork_initial_state(
        initial, spec, registry, tmp_path / "b", gzip_threshold_bytes=1_000_000
    )
    assert a.read_bytes() == b.read_bytes()
