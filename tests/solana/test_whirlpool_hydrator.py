"""Validation test for PRD US-001 line 216.

``get_program_accounts_at_slot(WHIRLPOOL_PROGRAM, known_corpus_slot)`` returns
account bytes that, when parsed by the 2.3-reference Whirlpool
``StateHydrator``, reproduce the expected pool reserves recorded in
``solana-plans/calibration/corpus/<slot>/manifest.yaml``.

The committed fixture for slot 160_000_001 is SYNTHETIC — see the manifest's
preamble. The validation is structural (parser produces the values the
manifest declares), not a real-mainnet calibration claim; that arrives with
2.4 once archival data is pulled.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from defi_sim_solana.replay.account_client import (
    clear_program_accounts_cache,
    get_program_accounts_at_slot,
)
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import StateHydrator
from defi_sim_solana.replay.whirlpool_hydrator import (
    WHIRLPOOL_POOL_DISCRIMINATOR,
    WHIRLPOOL_PROGRAM,
    WhirlpoolPoolFragment,
    WhirlpoolStateHydrator,
)

CORPUS_SLOT = 160_000_001
# Additional synthetic entry-gate corpus slots committed alongside 160_000_001
# for PRD line 254 ("at least three entry-gate slots"). Each slot's
# Whirlpool program-accounts fixture must round-trip through the
# 2.3-reference hydrator and match its manifest.
ENTRY_GATE_CORPUS_SLOTS: tuple[int, ...] = (160_000_001, 234_000_000)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_program_accounts_cache()


def _load_manifest_pool_reserves(
    slot: int = CORPUS_SLOT,
) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    """Parse the hand-curated manifest's ``pool_reserves`` + ``pool_tick_current_index``.

    Tiny inline YAML reader rather than a PyYAML dependency: the manifest
    keeps the same hand-rolled scalar/list shape as
    ``tools/cache_corpus_slot.py``.
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


def test_get_program_accounts_at_slot_matches_manifest_reserves() -> None:
    """PRD line 216 — corpus path + 2.3-reference hydrator + manifest."""
    expected_reserves, expected_ticks = _load_manifest_pool_reserves()
    assert expected_reserves, (
        "manifest.yaml at slot 160_000_001 has no pool_reserves entries; "
        "the line-216 validation requires at least one expected pool."
    )

    snapshot = get_program_accounts_at_slot(WHIRLPOOL_PROGRAM, CORPUS_SLOT)
    assert snapshot.program_id == WHIRLPOOL_PROGRAM
    assert snapshot.slot == CORPUS_SLOT
    assert len(snapshot.accounts) >= len(expected_reserves)

    hydrator = WhirlpoolStateHydrator()
    parsed: dict[str, tuple[int, int]] = {}
    parsed_ticks: dict[str, int] = {}
    for record in snapshot.accounts:
        pool = hydrator.parse_pool(record.pubkey, record.account_data)
        parsed[pool.pubkey] = pool.reserve_proxy
        parsed_ticks[pool.pubkey] = pool.tick_current_index

    for pubkey, expected in expected_reserves.items():
        assert parsed.get(pubkey) == expected, (
            f"hydrator reserves for {pubkey} ({parsed.get(pubkey)}) do not "
            f"match manifest ({expected})."
        )
    for pubkey, expected_tick in expected_ticks.items():
        assert parsed_ticks.get(pubkey) == expected_tick


def test_whirlpool_hydrator_rejects_short_payload() -> None:
    hydrator = WhirlpoolStateHydrator()
    with pytest.raises(ValueError, match="need at least"):
        hydrator.parse_pool("POOL", b"\x00" * 10)
    with pytest.raises(ValueError, match="need at least"):
        hydrator.parse_account("POOL", b"\x00" * 10)


def test_whirlpool_hydrator_program_id_and_schema_version() -> None:
    assert WhirlpoolStateHydrator.program_id == WHIRLPOOL_PROGRAM
    assert isinstance(WhirlpoolStateHydrator.schema_version, int)
    assert WhirlpoolStateHydrator.schema_version >= 1


@pytest.mark.parametrize("slot", ENTRY_GATE_CORPUS_SLOTS)
def test_entry_gate_corpus_slot_round_trips_through_hydrator(slot: int) -> None:
    """PRD line 254 — every committed entry-gate corpus slot must be
    structurally valid: its Whirlpool program-accounts fixture loads
    offline and parses to the values declared in its manifest."""
    expected_reserves, expected_ticks = _load_manifest_pool_reserves(slot)
    assert expected_reserves, (
        f"manifest.yaml at slot {slot} has no pool_reserves entries; "
        "every committed entry-gate corpus slot must declare at least one."
    )

    snapshot = get_program_accounts_at_slot(WHIRLPOOL_PROGRAM, slot)
    assert snapshot.slot == slot
    assert len(snapshot.accounts) >= len(expected_reserves)

    hydrator = WhirlpoolStateHydrator()
    for record in snapshot.accounts:
        pool = hydrator.parse_pool(record.pubkey, record.account_data)
        if pool.pubkey in expected_reserves:
            assert pool.reserve_proxy == expected_reserves[pool.pubkey]
        if pool.pubkey in expected_ticks:
            assert pool.tick_current_index == expected_ticks[pool.pubkey]


# --- 2.3b ABC-conformance tests (PRD line 721) ----------------------------


def test_whirlpool_hydrator_inherits_state_hydrator_abc() -> None:
    """PRD line 721 — the 2.3b reference impl must satisfy the
    :class:`StateHydrator` ABC so ``ForkLoader`` can drive it through the
    framework without duck-typing."""
    assert issubclass(WhirlpoolStateHydrator, StateHydrator)
    # ABC abstract methods are concrete on the subclass:
    h = WhirlpoolStateHydrator()
    assert isinstance(h, StateHydrator)


def test_whirlpool_hydrator_account_filters_includes_pool_and_tick_array() -> None:
    """The hydrator returns two 8-byte Anchor filters — one for the
    Whirlpool pool struct and one for ``TickArray`` (FixedTickArray) — so
    a single ``getProgramAccounts`` walk under ``ForkLoader`` admits both
    shapes the swap path needs."""
    from defi_sim_solana.replay.whirlpool_hydrator import (
        WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR,
    )

    filters = WhirlpoolStateHydrator().account_filters()
    assert len(filters) == 2
    discs = {f.discriminator for f in filters}
    assert discs == {WHIRLPOOL_POOL_DISCRIMINATOR, WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR}
    for f in filters:
        assert f.pubkey_allowlist is None
        assert len(f.discriminator) == 8


def test_whirlpool_hydrator_oracle_dependencies_is_empty() -> None:
    """Whirlpool state has no oracle dependencies — pools are
    self-contained CLMM accounts."""
    assert WhirlpoolStateHydrator().oracle_dependencies() == []


def test_whirlpool_hydrator_parse_account_returns_initial_state_fragment() -> None:
    """``parse_account`` must return an ABC-shaped
    :class:`InitialStateFragment` so :class:`ForkLoader` can merge it
    directly into the ``InitialState`` value object."""
    hydrator = WhirlpoolStateHydrator()
    snapshot = get_program_accounts_at_slot(WHIRLPOOL_PROGRAM, CORPUS_SLOT)
    assert snapshot.accounts, "fixture has no accounts to parse"
    record = snapshot.accounts[0]

    fragment = hydrator.parse_account(record.pubkey, record.account_data)

    assert isinstance(fragment, InitialStateFragment)
    assert fragment.kind == "pool"
    assert fragment.protocol_model == "Whirlpool"
    assert fragment.pubkey == record.pubkey
    assert fragment.owner is None
    # Payload preserves the typed-pool view so downstream consumers can
    # read either the raw fields or the CLMM reserve proxy.
    typed = hydrator.parse_pool(record.pubkey, record.account_data)
    assert fragment.payload["liquidity"] == typed.liquidity
    assert fragment.payload["sqrt_price_x64"] == typed.sqrt_price_x64
    assert fragment.payload["tick_current_index"] == typed.tick_current_index
    assert tuple(fragment.payload["reserve_proxy"]) == typed.reserve_proxy


def test_whirlpool_hydrator_parse_account_payload_is_json_safe() -> None:
    """Cache key keying (PRD line 526) round-trips fragments through
    ``InitialState.to_json`` / ``from_json``; the Whirlpool payload must
    serialise without coercion (no Decimal, no bytes)."""
    import json

    hydrator = WhirlpoolStateHydrator()
    snapshot = get_program_accounts_at_slot(WHIRLPOOL_PROGRAM, CORPUS_SLOT)
    fragment = hydrator.parse_account(
        snapshot.accounts[0].pubkey, snapshot.accounts[0].account_data
    )
    encoded = json.dumps(dict(fragment.payload), sort_keys=True)
    assert json.loads(encoded) == dict(fragment.payload)


def test_whirlpool_pool_fragment_typed_view_unchanged() -> None:
    """Regression guard: ``WhirlpoolPoolFragment`` is still the typed view
    used by the manifest validator path, even after the ABC retrofit."""
    f = WhirlpoolPoolFragment(
        pubkey="POOL", liquidity=10, sqrt_price_x64=20, tick_current_index=-5
    )
    assert f.reserve_proxy == (10, 20)
