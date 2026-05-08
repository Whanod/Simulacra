"""Tests for ``InitialState`` / ``InitialStateFragment`` (PRD US-003 line 419)."""

from __future__ import annotations

from defi_sim.engine.initial_state import InitialState, InitialStateFragment


def _frag(
    kind: str = "pool",
    protocol: str = "whirlpool",
    pubkey: str = "Pool1",
    owner: str | None = None,
    **payload: object,
) -> InitialStateFragment:
    return InitialStateFragment(
        kind=kind,  # type: ignore[arg-type]
        protocol_model=protocol,
        pubkey=pubkey,
        owner=owner,
        payload=payload,
    )


def test_fragment_is_frozen_and_value_equal() -> None:
    import dataclasses

    a = _frag(pubkey="A", reserve_a=100)
    b = _frag(pubkey="A", reserve_a=100)
    assert a == b
    # PRD specifies frozen=True so fields cannot be reassigned after construction.
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        a.pubkey = "B"  # type: ignore[misc]


def test_initial_state_default_fragments_empty() -> None:
    s = InitialState(slot=42)
    assert s.slot == 42
    assert s.fragments == []
    assert s.protocols() == []


def test_merge_single_fragment_appends() -> None:
    s = InitialState(slot=1)
    f = _frag(pubkey="P1")
    s.merge(f)
    assert s.fragments == [f]


def test_merge_iterable_extends_in_order() -> None:
    s = InitialState(slot=1)
    fs = [_frag(pubkey="P1"), _frag(pubkey="P2")]
    s.merge(fs)
    assert s.fragments == fs


def test_merge_initial_state_extends() -> None:
    a = InitialState(slot=1, fragments=[_frag(pubkey="P1")])
    b = InitialState(slot=1, fragments=[_frag(pubkey="P2"), _frag(pubkey="P3")])
    a.merge(b)
    assert [f.pubkey for f in a.fragments] == ["P1", "P2", "P3"]


def test_by_protocol_filters_preserving_order() -> None:
    s = InitialState(slot=1)
    s.merge(
        [
            _frag(pubkey="W1", protocol="whirlpool"),
            _frag(pubkey="M1", protocol="marginfi"),
            _frag(pubkey="W2", protocol="whirlpool"),
        ]
    )
    whirl = s.by_protocol("whirlpool")
    assert [f.pubkey for f in whirl] == ["W1", "W2"]
    assert s.by_protocol("missing") == []


def test_by_kind_filters_preserving_order() -> None:
    s = InitialState(slot=1)
    s.merge(
        [
            _frag(kind="pool", pubkey="P1"),
            _frag(kind="oracle_price", pubkey="O1", protocol="pyth-pull"),
            _frag(kind="pool", pubkey="P2"),
        ]
    )
    pools = s.by_kind("pool")  # type: ignore[arg-type]
    assert [f.pubkey for f in pools] == ["P1", "P2"]
    assert [f.pubkey for f in s.by_kind("oracle_price")] == ["O1"]  # type: ignore[arg-type]


def test_protocols_distinct_first_seen_order() -> None:
    s = InitialState(slot=1)
    s.merge(
        [
            _frag(pubkey="W1", protocol="whirlpool"),
            _frag(pubkey="M1", protocol="marginfi"),
            _frag(pubkey="W2", protocol="whirlpool"),
            _frag(pubkey="P1", protocol="pyth-pull"),
        ]
    )
    assert s.protocols() == ["whirlpool", "marginfi", "pyth-pull"]


def test_protocols_deterministic_under_repeat() -> None:
    s = InitialState(slot=1)
    for proto in ("a", "b", "a", "c", "b"):
        s.merge(_frag(protocol=proto, pubkey=f"K{proto}"))
    # The dict insertion order keeps "a" before "b" before "c".
    assert s.protocols() == ["a", "b", "c"]
