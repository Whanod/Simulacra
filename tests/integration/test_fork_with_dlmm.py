"""Integration tests for forking against the committed DLMM corpus (PRD US-003 lines 700-701).

These exercise the end-to-end :class:`ForkLoader` pipeline against the real
``DlmmStateHydrator`` (the 2.3c reference impl, see PRD line 722) and the
committed corpus fixture under
``solana-plans/calibration/corpus/<slot>/``. No network access required —
``get_program_accounts_at_slot`` resolves the corpus path before consulting any
historical backend.

The slot 161_000_001 fixture is intentionally synthetic; once 2.4 calibration
pulls real archival data, both the fixture *and* the manifest's
``pool_reserves`` / ``pool_active_id`` sections should be replaced with
mainnet-derived values without changing the test shape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from defi_sim.engine.fork import ForkSpec, ProtocolForkRequest
from defi_sim.engine.forkable import ForkableMarket
from defi_sim.engine.fork_loader import ForkLoader, ProtocolModelRegistry
from defi_sim.engine.initial_state import InitialState
from defi_sim_solana.replay import account_client as account_client_mod
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim_solana.replay.dlmm_hydrator import DlmmStateHydrator

DLMM_CORPUS_SLOT = 161_000_001
DLMM_PROTOCOL_MODEL = "MeteoraDlmm"


@pytest.fixture(autouse=True)
def _reset_program_accounts_cache() -> None:
    account_client_mod.clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


class _DlmmForkableMarket(ForkableMarket):
    """Minimal ``ForkableMarket`` wrapping the 2.3c DLMM hydrator.

    The integration test only exercises the hydration path through
    :class:`ForkLoader` — pool fragments materialize via the hydrator's
    ``parse_account`` regardless of whether ``from_initial_state`` is invoked.
    The factory raises if called so a future regression that wires the
    materializer into this test fails loudly instead of silently constructing
    a stub market. (Mirrors the sibling ``_WhirlpoolForkableMarket`` in
    ``test_fork_with_whirlpool.py``.)
    """

    state_hydrator = DlmmStateHydrator()

    @classmethod
    def from_initial_state(cls, fragments, *, parameters, numeric_mode):
        raise AssertionError(
            "this integration test asserts loader-level pool counts and "
            "active-bin distribution; materialize_fork is exercised in "
            "tests/engine/test_fork_hydration.py."
        )


def _expected_manifest_pool_metrics(
    slot: int,
) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    """Inline manifest YAML reader (mirrors ``test_fork_with_whirlpool.py``).

    Returns ``(reserves_by_pubkey, active_ids_by_pubkey)`` where
    ``reserves_by_pubkey`` maps a pool pubkey to its
    ``(active_id, bin_step)`` reserve-proxy tuple and
    ``active_ids_by_pubkey`` maps the same pubkeys to their declared active
    bin id from the manifest's ``pool_active_id`` section.
    """
    manifest_path: Path = corpus_root() / str(slot) / "manifest.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    reserves: dict[str, tuple[int, int]] = {}
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
        elif section == "pool_active_id":
            m = scalar_re.match(line)
            if m:
                active_ids[m.group(1)] = int(m.group(2))
    return reserves, active_ids


def _build_loader() -> ForkLoader:
    """Construct a corpus-only ``ForkLoader`` — no historical backend needed.

    The loader resolves accounts via ``get_program_accounts_at_slot``, which
    consults the committed corpus fixture before any backend. Passing
    ``historical_backend=None`` is therefore safe as long as the slot has a
    committed fixture (which 161_000_001 does).
    """
    registry = ProtocolModelRegistry({DLMM_PROTOCOL_MODEL: _DlmmForkableMarket})
    return ForkLoader(registry, historical_backend=None)


def test_fork_dlmm_at_known_slot_has_known_bin_distribution() -> None:
    """PRD line 700 — fork a DLMM pool at a known slot, assert the
    active-bin distribution matches mainnet.

    DLMM ``LbPair`` accounts pin their price state with an ``active_id``
    (the currently-quoted bin) and a ``bin_step`` (price discreteness). The
    "distribution" of active ids across the forked pools is a multi-pool
    fingerprint: a single-pool fork couldn't distinguish "fork actually
    parsed both pools" from "fork accidentally returned the same pool
    twice". Asserting the multiset of ``active_id`` values matches the
    manifest ensures both pools round-trip end-to-end through the loader
    AND that the parser correctly read each pool's bin pointer.
    """
    expected_reserves, expected_active_ids = _expected_manifest_pool_metrics(
        DLMM_CORPUS_SLOT
    )
    expected_pool_count = len(expected_reserves)
    assert expected_pool_count >= 2, (
        f"manifest.yaml at slot {DLMM_CORPUS_SLOT} must declare at least "
        "two pools so the active-bin distribution assertion is meaningful "
        "(a single-pool distribution collapses to the pool_count assertion)."
    )

    loader = _build_loader()
    initial = loader.load(
        ForkSpec(
            slot=DLMM_CORPUS_SLOT,
            protocols=[ProtocolForkRequest(protocol_model=DLMM_PROTOCOL_MODEL)],
        )
    )

    assert isinstance(initial, InitialState)
    assert initial.slot == DLMM_CORPUS_SLOT

    pool_fragments = initial.by_kind("pool")
    assert len(pool_fragments) == expected_pool_count, (
        f"forked DLMM pool count {len(pool_fragments)} does not match "
        f"manifest's expected count {expected_pool_count}; "
        f"parsed pubkeys={[f.pubkey for f in pool_fragments]}, "
        f"manifest pubkeys={sorted(expected_reserves)}."
    )
    assert {f.pubkey for f in pool_fragments} == set(expected_reserves), (
        "DLMM pool fragment pubkeys do not match the manifest's "
        "pool_reserves keys."
    )

    parsed_active_ids = sorted(
        int(f.payload["active_id"]) for f in pool_fragments
    )
    expected_distribution = sorted(expected_active_ids.values())
    assert parsed_active_ids == expected_distribution, (
        f"forked active-bin distribution {parsed_active_ids} does not match "
        f"manifest expected {expected_distribution}."
    )

    for fragment in pool_fragments:
        assert fragment.protocol_model == DLMM_PROTOCOL_MODEL
        assert fragment.kind == "pool"


def test_fork_dlmm_pool_active_bin_matches_mainnet() -> None:
    """PRD line 701 — assert each forked DLMM pool's active bin matches what
    mainnet showed at that slot.

    The 2.3c-reference reserve proxy (per PRD line 722 / manifest preamble)
    is the ``(active_id, bin_step)`` pair. The committed fixture for slot
    161_000_001 is synthetic; once 2.4 archival pulls land, the manifest gets
    each DLMM pool's mainnet values and this test starts asserting against
    true on-chain state without any test-shape change.
    """
    expected_reserves, expected_active_ids = _expected_manifest_pool_metrics(
        DLMM_CORPUS_SLOT
    )
    assert expected_reserves, (
        f"manifest.yaml at slot {DLMM_CORPUS_SLOT} declares no "
        "pool_reserves; cannot assert active bin matches mainnet."
    )

    loader = _build_loader()
    initial = loader.load(
        ForkSpec(
            slot=DLMM_CORPUS_SLOT,
            protocols=[ProtocolForkRequest(protocol_model=DLMM_PROTOCOL_MODEL)],
        )
    )

    parsed_reserves: dict[str, tuple[int, int]] = {}
    parsed_active_ids: dict[str, int] = {}
    for fragment in initial.by_kind("pool"):
        payload = fragment.payload
        parsed_reserves[fragment.pubkey] = tuple(payload["reserve_proxy"])  # type: ignore[assignment]
        parsed_active_ids[fragment.pubkey] = int(payload["active_id"])

    for pubkey, expected in expected_reserves.items():
        assert parsed_reserves.get(pubkey) == expected, (
            f"forked reserve_proxy for {pubkey} ({parsed_reserves.get(pubkey)}) "
            f"does not match manifest expected {expected}."
        )
    for pubkey, expected_active_id in expected_active_ids.items():
        assert parsed_active_ids.get(pubkey) == expected_active_id, (
            f"forked active_id for {pubkey} "
            f"({parsed_active_ids.get(pubkey)}) does not match manifest "
            f"expected {expected_active_id}."
        )
