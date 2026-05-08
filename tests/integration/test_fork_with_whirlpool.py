"""Integration tests for forking against the committed Whirlpool corpus (PRD US-003 line 695-696).

These exercise the end-to-end :class:`ForkLoader` pipeline against the real
``WhirlpoolStateHydrator`` (the 2.3b reference impl, see PRD line 721) and the
committed corpus fixture under
``solana-plans/calibration/corpus/<slot>/``. No network access required —
``get_program_accounts_at_slot`` resolves the corpus path before consulting any
historical backend.

The slot 160_000_001 fixture is intentionally synthetic; once 2.4 calibration
pulls real archival data, both the fixture *and* the ``expected_pool_count``
constant below should be replaced with mainnet-derived values without changing
the test shape.
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
from defi_sim_solana.replay.whirlpool_hydrator import WhirlpoolStateHydrator

WHIRLPOOL_CORPUS_SLOT = 160_000_001
WHIRLPOOL_PROTOCOL_MODEL = "Whirlpool"


@pytest.fixture(autouse=True)
def _reset_program_accounts_cache() -> None:
    account_client_mod.clear_program_accounts_cache()
    account_client_mod._BACKEND_REGISTRY.clear()


class _WhirlpoolForkableMarket(ForkableMarket):
    """Minimal ``ForkableMarket`` wrapping the 2.3b Whirlpool hydrator.

    The integration test only exercises the hydration path through
    :class:`ForkLoader` — pool fragments materialize via the hydrator's
    ``parse_account`` regardless of whether ``from_initial_state`` is invoked.
    The factory raises if called so a future regression that wires the
    materializer into this test fails loudly instead of silently constructing
    a stub market.
    """

    state_hydrator = WhirlpoolStateHydrator()

    @classmethod
    def from_initial_state(cls, fragments, *, parameters, numeric_mode):
        raise AssertionError(
            "this integration test asserts loader-level pool counts; "
            "materialize_fork is exercised in tests/engine/test_fork_hydration.py."
        )


def _expected_manifest_pool_metrics(
    slot: int,
) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    """Inline manifest YAML reader (mirrors tests/solana/test_whirlpool_hydrator.py).

    Pulled into this file so the integration test does not import private
    helpers across packages. Returns ``(reserves_by_pubkey, ticks_by_pubkey)``.
    """
    manifest_path: Path = corpus_root() / str(slot) / "manifest.yaml"
    text = manifest_path.read_text(encoding="utf-8")
    reserves: dict[str, tuple[int, int]] = {}
    ticks: dict[str, int] = {}
    section: str | None = None
    list_re = re.compile(r'^\s*"([^"]+)"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]\s*$')
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
    return reserves, ticks


def _build_loader() -> ForkLoader:
    """Construct a corpus-only ``ForkLoader`` — no historical backend needed.

    The loader resolves accounts via ``get_program_accounts_at_slot``, which
    consults the committed corpus fixture before any backend. Passing
    ``historical_backend=None`` is therefore safe as long as the slot has a
    committed fixture (which 160_000_001 does).
    """
    registry = ProtocolModelRegistry(
        {WHIRLPOOL_PROTOCOL_MODEL: _WhirlpoolForkableMarket}
    )
    return ForkLoader(registry, historical_backend=None)


def test_fork_whirlpool_at_known_slot_has_known_pool_count() -> None:
    """PRD line 695 — fork Whirlpool at a known historical slot, assert pool
    count matches a precomputed expected value.

    The expected count is derived from the manifest's ``pool_reserves``
    section so the test stays in lockstep with the fixture: adding a new
    pool to the fixture requires adding a manifest entry for it, and the
    test fails until both are present.
    """
    expected_reserves, _ = _expected_manifest_pool_metrics(WHIRLPOOL_CORPUS_SLOT)
    expected_pool_count = len(expected_reserves)
    assert expected_pool_count >= 1, (
        f"manifest.yaml at slot {WHIRLPOOL_CORPUS_SLOT} must declare at least "
        "one pool under pool_reserves; the integration test cannot assert a "
        "zero-pool fork is meaningful."
    )

    loader = _build_loader()
    initial = loader.load(
        ForkSpec(
            slot=WHIRLPOOL_CORPUS_SLOT,
            protocols=[
                ProtocolForkRequest(protocol_model=WHIRLPOOL_PROTOCOL_MODEL)
            ],
        )
    )

    assert isinstance(initial, InitialState)
    assert initial.slot == WHIRLPOOL_CORPUS_SLOT

    pool_fragments = initial.by_kind("pool")
    assert len(pool_fragments) == expected_pool_count, (
        f"forked Whirlpool pool count {len(pool_fragments)} does not match "
        f"manifest's expected count {expected_pool_count}; "
        f"parsed pubkeys={[f.pubkey for f in pool_fragments]}, "
        f"manifest pubkeys={sorted(expected_reserves)}."
    )
    assert {f.pubkey for f in pool_fragments} == set(expected_reserves), (
        "pool fragment pubkeys do not match the manifest's pool_reserves keys."
    )
    for fragment in pool_fragments:
        assert fragment.protocol_model == WHIRLPOOL_PROTOCOL_MODEL
        assert fragment.kind == "pool"


def test_fork_whirlpool_pool_reserves_match_mainnet() -> None:
    """PRD line 696 — assert the SOL/USDC Whirlpool pool's reserves at fork
    time match what mainnet showed at that slot.

    The 2.3-reference reserve proxy (per PRD line 216 / manifest preamble) is
    the ``(liquidity, sqrt_price_x64)`` pair. The committed fixture for slot
    160_000_001 is synthetic; once 2.4 archival pulls land, the manifest gets
    the real SOL/USDC pool's mainnet values and this test starts asserting
    against true on-chain state without any test-shape change.
    """
    expected_reserves, expected_ticks = _expected_manifest_pool_metrics(
        WHIRLPOOL_CORPUS_SLOT
    )
    assert expected_reserves, (
        f"manifest.yaml at slot {WHIRLPOOL_CORPUS_SLOT} declares no "
        "pool_reserves; cannot assert reserves match mainnet."
    )

    loader = _build_loader()
    initial = loader.load(
        ForkSpec(
            slot=WHIRLPOOL_CORPUS_SLOT,
            protocols=[
                ProtocolForkRequest(protocol_model=WHIRLPOOL_PROTOCOL_MODEL)
            ],
        )
    )

    parsed_reserves: dict[str, tuple[int, int]] = {}
    parsed_ticks: dict[str, int] = {}
    for fragment in initial.by_kind("pool"):
        payload = fragment.payload
        parsed_reserves[fragment.pubkey] = tuple(payload["reserve_proxy"])  # type: ignore[assignment]
        parsed_ticks[fragment.pubkey] = int(payload["tick_current_index"])

    for pubkey, expected in expected_reserves.items():
        assert parsed_reserves.get(pubkey) == expected, (
            f"forked reserve_proxy for {pubkey} ({parsed_reserves.get(pubkey)}) "
            f"does not match manifest expected {expected}."
        )
    for pubkey, expected_tick in expected_ticks.items():
        assert parsed_ticks.get(pubkey) == expected_tick, (
            f"forked tick_current_index for {pubkey} "
            f"({parsed_ticks.get(pubkey)}) does not match manifest expected "
            f"{expected_tick}."
        )
