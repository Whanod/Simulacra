"""US-009 integration test (PRD line 695): ALTs round-trip through the API.

Submits a Solana-flavored spec with ``alts`` to ``/simulations/build``,
asserts the engine state has the seeded ``AddressLookupTable`` registry,
then submits a ``VersionedTransaction`` referencing the ALT through the
admit path and asserts it lands. Exercises the same end-to-end wiring as
``tests/engine/test_versioned_transactions.py::
test_admit_accepts_30_accounts_when_covered_by_seeded_alt`` but driven
through the API surface so the spec → schema → engine round-trip is covered.
"""

from __future__ import annotations

from defi_sim.core.types import Action
from defi_sim.engine.transactions import AddressLookupTable, VersionedTransaction

from defi_sim_api import state as sim_state


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


def test_simulation_with_alts_in_spec(client) -> None:
    resp = client.post("/simulations/build", json=SOLANA_SPEC_WITH_ALTS)
    assert resp.status_code == 201, resp.text
    sim_id = resp.json()["simulation_id"]

    entry = sim_state.get(sim_id)
    assert entry is not None
    engine = entry.engine

    assert set(engine.alts.keys()) == {"alt-hot-pool", "alt-secondary"}
    hot = engine.alts["alt-hot-pool"]
    assert isinstance(hot, AddressLookupTable)
    assert hot.entries == [f"acct-{i}" for i in range(30)]
    assert engine.alts["alt-secondary"].entries == ["acct-A", "acct-B"]

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

    # PRD US-009 line 657: the wrapper is what gets *submitted* (size +
    # ALT-resolved blockhash check), but the inner instructions are what
    # the engine *executes*. Admit unwraps and returns the inner action(s).
    assert admitted == [action]
    assert dropped == []
