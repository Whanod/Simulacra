"""Validation tests for PRD US-003 line 722 (2.3c DLMM ``StateHydrator``).

The DLMM corpus fixture lands with 2.4 calibration; until then this test
suite exercises the parser via synthetic ``LbPair`` bytes constructed
in-test against the documented Meteora DLMM account layout. The
structural contract — ABC conformance, discriminator pinning,
``parse_account`` → :class:`InitialStateFragment`, JSON-safe payload — is
identical to the Whirlpool 2.3b reference impl and is what 2.4 will rely
on when wiring the DLMM corpus.
"""

from __future__ import annotations

import json

import pytest

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import StateHydrator
from defi_sim_solana.replay.dlmm_hydrator import (
    DLMM_LB_PAIR_DISCRIMINATOR,
    DLMM_PROGRAM,
    DlmmLbPairFragment,
    DlmmStateHydrator,
)


def _build_lb_pair_bytes(active_id: int, bin_step: int) -> bytes:
    """Build a minimum-prefix LbPair account payload (82 bytes).

    Layout matches the documented Meteora DLMM ``LbPair`` struct: 8-byte
    discriminator, 32 bytes static_parameters, 32 bytes v_parameters,
    1 byte bump_seed, 2 bytes bin_step_seed, 1 byte pair_type, 4 bytes
    active_id (i32 LE), 2 bytes bin_step (u16 LE).
    """
    buf = bytearray(82)
    buf[0:8] = DLMM_LB_PAIR_DISCRIMINATOR
    # Bytes 8..76 are parser-irrelevant for the reserve-proxy validation;
    # leave them zeroed.
    buf[76:80] = active_id.to_bytes(4, "little", signed=True)
    buf[80:82] = bin_step.to_bytes(2, "little", signed=False)
    return bytes(buf)


def test_dlmm_hydrator_program_id_and_schema_version() -> None:
    assert DlmmStateHydrator.program_id == DLMM_PROGRAM
    assert isinstance(DlmmStateHydrator.schema_version, int)
    assert DlmmStateHydrator.schema_version >= 1


def test_dlmm_hydrator_inherits_state_hydrator_abc() -> None:
    """PRD line 722 — the 2.3c reference impl must satisfy the
    :class:`StateHydrator` ABC so ``ForkLoader`` can drive it through the
    framework without duck-typing."""
    assert issubclass(DlmmStateHydrator, StateHydrator)
    h = DlmmStateHydrator()
    assert isinstance(h, StateHydrator)


def test_dlmm_hydrator_account_filters_uses_real_lb_pair_discriminator() -> None:
    """The 8-byte filter is the real Anchor ``account:LbPair``
    discriminator, narrowing ``getProgramAccounts`` to ``LbPair`` accounts
    and excluding sibling shapes (Position / BinArray) under the same
    program id."""
    filters = DlmmStateHydrator().account_filters()
    assert len(filters) == 1
    assert filters[0].discriminator == DLMM_LB_PAIR_DISCRIMINATOR
    assert len(DLMM_LB_PAIR_DISCRIMINATOR) == 8
    assert filters[0].pubkey_allowlist is None


def test_dlmm_hydrator_oracle_dependencies_is_empty() -> None:
    """DLMM ``LbPair`` state has no oracle dependencies — pools are
    self-contained discrete-bin AMM accounts."""
    assert DlmmStateHydrator().oracle_dependencies() == []


def test_dlmm_hydrator_parse_lb_pair_reads_active_id_and_bin_step() -> None:
    hydrator = DlmmStateHydrator()
    payload = _build_lb_pair_bytes(active_id=-12345, bin_step=25)
    pair = hydrator.parse_lb_pair("LBPAIR", payload)
    assert isinstance(pair, DlmmLbPairFragment)
    assert pair.pubkey == "LBPAIR"
    assert pair.active_id == -12345
    assert pair.bin_step == 25
    assert pair.reserve_proxy == (-12345, 25)


def test_dlmm_hydrator_parse_lb_pair_handles_signed_active_id() -> None:
    """``active_id`` is i32 — large positive and negative bins must round-trip."""
    hydrator = DlmmStateHydrator()
    for active_id in (0, 1, -1, 2_147_483_647, -2_147_483_648):
        pair = hydrator.parse_lb_pair(
            "LB", _build_lb_pair_bytes(active_id=active_id, bin_step=10)
        )
        assert pair.active_id == active_id


def test_dlmm_hydrator_rejects_short_payload() -> None:
    hydrator = DlmmStateHydrator()
    with pytest.raises(ValueError, match="need at least"):
        hydrator.parse_lb_pair("LB", b"\x00" * 10)
    with pytest.raises(ValueError, match="need at least"):
        hydrator.parse_account("LB", b"\x00" * 10)


def test_dlmm_hydrator_parse_account_returns_initial_state_fragment() -> None:
    """``parse_account`` must return an ABC-shaped
    :class:`InitialStateFragment` so :class:`ForkLoader` can merge it
    directly into the ``InitialState`` value object."""
    hydrator = DlmmStateHydrator()
    payload = _build_lb_pair_bytes(active_id=8388, bin_step=20)
    fragment = hydrator.parse_account("LBPAIR", payload)

    assert isinstance(fragment, InitialStateFragment)
    assert fragment.kind == "pool"
    assert fragment.protocol_model == "MeteoraDlmm"
    assert fragment.pubkey == "LBPAIR"
    assert fragment.owner is None
    assert fragment.payload["active_id"] == 8388
    assert fragment.payload["bin_step"] == 20
    assert tuple(fragment.payload["reserve_proxy"]) == (8388, 20)


def test_dlmm_hydrator_parse_account_payload_is_json_safe() -> None:
    """Cache key keying (PRD line 526) round-trips fragments through
    ``InitialState.to_json`` / ``from_json``; the DLMM payload must
    serialise without coercion (no Decimal, no bytes)."""
    hydrator = DlmmStateHydrator()
    payload = _build_lb_pair_bytes(active_id=100, bin_step=50)
    fragment = hydrator.parse_account("LBPAIR", payload)
    encoded = json.dumps(dict(fragment.payload), sort_keys=True)
    assert json.loads(encoded) == dict(fragment.payload)


def test_dlmm_lb_pair_fragment_reserve_proxy_pairs_active_id_and_bin_step() -> None:
    """Regression guard: ``DlmmLbPairFragment.reserve_proxy`` returns the
    DLMM-canonical (active_id, bin_step) pair so the manifest validator
    path can use the same per-pool tuple shape as Whirlpool's CLMM proxy."""
    f = DlmmLbPairFragment(pubkey="LB", active_id=42, bin_step=15)
    assert f.reserve_proxy == (42, 15)
