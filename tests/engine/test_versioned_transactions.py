"""VersionedTransaction / ALT spec integration tests (US-009, PRD line 687).

Covers the ALT-first-class-in-spec contract (PRD line 676): an ``alts``
list on the top-level ``RunSpec`` is materialized into
``engine.alts: dict[AltId, AddressLookupTable]`` on engine init, so any
``VersionedTransaction`` submitted later can resolve account references
against the table and bring its wire-format size under the 1232-byte
packet limit.
"""

from __future__ import annotations

import copy

from defi_sim.core.types import Action
from defi_sim.engine.api import build_engine
from defi_sim.engine.execution import DropReason
from defi_sim.engine.specs import AltSpec, RunSpec
from defi_sim.engine.transactions import (
    MAX_TX_SIZE_BYTES,
    AddressLookupTable,
    VersionedTransaction,
    compute_tx_size,
)


SOLANA_SPEC_WITH_ALTS: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "trader-1",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000, "SOL": 1_000_000},
        },
    ],
    "alts": [
        {"id": "alt-hot-pool", "entries": [f"acct-{i}" for i in range(30)]},
        {"id": "alt-secondary", "entries": ["acct-A", "acct-B"]},
    ],
    "num_rounds": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def test_alt_spec_round_trips_through_run_spec() -> None:
    spec = RunSpec.from_dict(copy.deepcopy(SOLANA_SPEC_WITH_ALTS))
    assert [alt.id for alt in spec.alts] == ["alt-hot-pool", "alt-secondary"]
    assert spec.alts[0].entries[0] == "acct-0"
    assert len(spec.alts[0].entries) == 30
    assert spec.alts[1].entries == ["acct-A", "acct-B"]


def test_run_spec_alts_default_to_empty_list() -> None:
    spec = RunSpec.from_dict(
        {
            "market": copy.deepcopy(SOLANA_SPEC_WITH_ALTS["market"]),
            "agents": copy.deepcopy(SOLANA_SPEC_WITH_ALTS["agents"]),
            "num_rounds": 1,
            "seed": 7,
        }
    )
    assert spec.alts == []


def test_alt_seeded_from_spec_on_engine_init() -> None:
    """PRD line 685/692: engine.alts is keyed by AltId after build_engine()."""
    engine = build_engine(copy.deepcopy(SOLANA_SPEC_WITH_ALTS))

    assert set(engine.alts.keys()) == {"alt-hot-pool", "alt-secondary"}
    hot = engine.alts["alt-hot-pool"]
    assert isinstance(hot, AddressLookupTable)
    assert hot.id == "alt-hot-pool"
    assert hot.entries == [f"acct-{i}" for i in range(30)]
    assert engine.alts["alt-secondary"].entries == ["acct-A", "acct-B"]


def test_engine_alts_empty_when_spec_omits_alts() -> None:
    spec_no_alts = copy.deepcopy(SOLANA_SPEC_WITH_ALTS)
    spec_no_alts.pop("alts")
    engine = build_engine(spec_no_alts)
    assert engine.alts == {}


def test_alt_spec_constructed_directly() -> None:
    alt = AltSpec(id="alt-x", entries=["a", "b", "c"])
    assert alt.id == "alt-x"
    assert alt.entries == ["a", "b", "c"]


def test_tx_size_no_alt_below_limit_for_small_action() -> None:
    """PRD line 688: a small VersionedTransaction (1 action, few accounts, no
    ALTs) sits well under the 1232-byte packet limit."""
    action = Action(agent_id="trader-1")
    object.__setattr__(action, "read_locks", frozenset({"acct-r1", "acct-r2"}))
    object.__setattr__(action, "write_locks", frozenset({"acct-w1"}))

    vtx = VersionedTransaction(actions=[action])

    size = compute_tx_size(vtx)

    assert size < MAX_TX_SIZE_BYTES
    # 1 sig-count + 64 sig + 3 msg-header + 3*32 account + 1 program_id = 165
    assert size == 165


def test_tx_size_with_30_accounts_no_alt_exceeds_1232() -> None:
    """PRD line 689: a VersionedTransaction wrapping 30 unique account
    references with no ALTs trips the 1232-byte packet cap.

    Bare 1-sig + 30 accounts is 1029 bytes (under 1232), so we push the size up
    via ``num_required_signatures=5`` (each sig adds 64 bytes). 5 sigs + 30
    accounts no-ALT lands at 1285 bytes, deliberately above the cap.
    """
    action = Action(agent_id="trader-1")
    accounts = frozenset(f"acct-{i}" for i in range(30))
    object.__setattr__(action, "read_locks", accounts)
    object.__setattr__(action, "write_locks", frozenset())

    vtx = VersionedTransaction(actions=[action], num_required_signatures=5)

    size = compute_tx_size(vtx)

    assert size > MAX_TX_SIZE_BYTES
    # 1 sig-count + 5*64 sig + 3 msg-header + 30*32 account + 1 program_id = 1285
    assert size == 1285


def test_admit_accepts_30_accounts_when_covered_by_seeded_alt() -> None:
    """PRD line 684: the 30-reference VersionedTransaction that is rejected
    without an ALT (5 sigs, 30 inline accounts → 1285 > 1232) is admitted
    when the same 30 references are covered by a 30-entry ALT seeded from
    the spec — the engine wires ``engine.alts`` into the admit-time size
    check so ``compute_tx_size`` collapses each account to 3 bytes.
    """
    engine = build_engine(copy.deepcopy(SOLANA_SPEC_WITH_ALTS))

    action = Action(agent_id="trader-1")
    accounts = frozenset(f"acct-{i}" for i in range(30))
    object.__setattr__(action, "read_locks", accounts)
    object.__setattr__(action, "write_locks", frozenset())

    vtx = VersionedTransaction(
        actions=[action],
        lookup_tables=["alt-hot-pool"],
        num_required_signatures=5,
    )

    admitted, dropped = engine._execution_model.admit([vtx], round=1)

    # PRD US-009 line 657: wrapper is submitted, inner instructions execute.
    # Admit returns the unwrapped inner action(s); the wrapper itself is
    # consumed once the size + ALT check pass.
    assert admitted == [action]
    assert dropped == []


def test_oversized_tx_dropped_with_correct_reason() -> None:
    """PRD line 691: a VersionedTransaction whose wire-format size exceeds
    the 1232-byte packet cap is dropped at admit with
    ``DropReason.TX_SIZE_EXCEEDED`` (PRD line 675).

    Same shape as ``test_tx_size_with_30_accounts_no_alt_exceeds_1232``
    (5 sigs + 30 inline accounts → 1285 bytes) but driven through
    ``execution_model.admit`` so the drop-reason wiring is exercised
    end-to-end, not just the size predicate.
    """
    engine = build_engine(copy.deepcopy(SOLANA_SPEC_WITH_ALTS))

    action = Action(agent_id="trader-1")
    accounts = frozenset(f"acct-{i}" for i in range(30))
    object.__setattr__(action, "read_locks", accounts)
    object.__setattr__(action, "write_locks", frozenset())

    vtx = VersionedTransaction(actions=[action], num_required_signatures=5)

    admitted, dropped = engine._execution_model.admit([vtx], round=1)

    assert admitted == []
    assert dropped == [(vtx, DropReason.TX_SIZE_EXCEEDED)]


def test_tx_size_with_alt_compresses_30_accounts_to_under_1232() -> None:
    """PRD line 690: 30 account refs covered by a 30-entry ALT compress to 3
    bytes each (table-index + account-index + flag) instead of 32, dropping
    the wire size from 1029 (no-ALT, 1-sig) to 159 bytes — well under the
    1232-byte packet cap.
    """
    action = Action(agent_id="trader-1")
    accounts = [f"acct-{i}" for i in range(30)]
    object.__setattr__(action, "read_locks", frozenset(accounts))
    object.__setattr__(action, "write_locks", frozenset())

    vtx = VersionedTransaction(actions=[action], lookup_tables=["alt-hot-pool"])
    alts = {"alt-hot-pool": AddressLookupTable(id="alt-hot-pool", entries=accounts)}

    size = compute_tx_size(vtx, alts=alts)

    assert size < MAX_TX_SIZE_BYTES
    # 1 sig-count + 1*64 sig + 3 msg-header + 30*3 alt-resolved + 1 program_id = 159
    assert size == 159
