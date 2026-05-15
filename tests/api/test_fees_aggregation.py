"""Phase 4.5 ``fees`` table coverage.

Two contracts under test:

* :meth:`PostgresArtifactStore._maybe_materialise_fees` populates the table
  from ``runs.result['fee_history']`` exactly once per terminal status, with
  per-token granularity preserved.
* :meth:`PostgresArtifactStore.query_fee_history` reconstructs the original
  nested ``list[dict[destination, dict[token, amount]]]`` shape that
  ``chartDataFromResult`` / ``sumLpFeesForToken`` consume.

The two together form the "round-trip" property the migration plan calls
out: ``fees`` rows for a given run must round-trip back to the engine's
``result.fee_history`` exactly once the run is terminal.
"""

from __future__ import annotations

import pytest

from defi_sim_api.backend.pg_store import PostgresArtifactStore


_TWO_DESTINATIONS_FEE_HISTORY = [
    # round 0: no fees
    {},
    # round 1: lp earns in both tokens, protocol earns in quote only
    {"lp": {"SOL": 1.5, "USDC": 25.0}, "protocol": {"USDC": 5.0}},
    # round 2: only burn
    {"burn": {"USDC": 0.25}},
]


@pytest.fixture()
def pg_store(pg_pool):
    return PostgresArtifactStore(pool=pg_pool)


def _create_completed_run(
    store: PostgresArtifactStore, run_id: str, *, result: dict
) -> None:
    # Engine convention: ``_current_round`` is incremented *before*
    # ``_record_round`` appends to ``_fee_history``, so after N rounds
    # both ``current_round`` and ``len(fee_history)`` equal N
    # (``simulation.py:386-388, 1028``). The pg view's fee_history list
    # is bounded by ``current_round`` rather than ``MAX(round)`` from the
    # fees table to keep trailing zero-fee rounds intact.
    store.create_run(
        run_id,
        spec={"market": {"type": "cfamm"}},
        status="completed",
        seed=1,
        market_type="cfamm",
        source="sync",
        simulation_id=run_id,
        current_round=len(result.get("fee_history") or []),
        summary={},
    )
    store.save_run_artifacts(run_id, result=result, events=[], round_snapshots=[])


def test_query_fee_history_round_trips_two_destinations(pg_store):
    """Per-(round, destination, token) rows reconstruct the engine shape."""
    _create_completed_run(
        pg_store,
        "fees-1",
        result={"fee_history": _TWO_DESTINATIONS_FEE_HISTORY},
    )
    reconstructed = pg_store.query_fee_history("fees-1")
    assert reconstructed == _TWO_DESTINATIONS_FEE_HISTORY


def test_query_fee_history_returns_empty_for_run_with_no_fees(pg_store):
    """A terminal run whose result has no fee_history materialises no rows;
    query_fee_history returns ``[]`` rather than raising or fabricating
    empty per-round dicts (we have no MAX(round) to bound the list)."""
    _create_completed_run(pg_store, "fees-2", result={})
    assert pg_store.query_fee_history("fees-2") == []


def test_query_fee_history_unknown_run_returns_empty(pg_store):
    assert pg_store.query_fee_history("no-such-run") == []


def test_materialise_fees_skips_non_terminal_runs(pg_store):
    """Live runs must not produce fees rows even if a partial result is
    written — the fee_history is incomplete, and the table is the source
    of truth once populated."""
    pg_store.create_run(
        "fees-live",
        spec={"market": {"type": "cfamm"}},
        status="live",
        seed=1,
        market_type="cfamm",
        source="streaming",
        simulation_id="fees-live",
        current_round=1,
        summary={},
    )
    pg_store.save_run_artifacts(
        "fees-live",
        result={"fee_history": _TWO_DESTINATIONS_FEE_HISTORY},
        events=[],
        round_snapshots=[],
    )
    assert pg_store.query_fee_history("fees-live") == []


def test_materialise_fees_via_update_run_when_status_flips_terminal(pg_store):
    """Phase 2's live-streaming path persists ``result`` before ``status``;
    the materialisation must catch the flip via ``update_run``."""
    pg_store.create_run(
        "fees-flip",
        spec={"market": {"type": "cfamm"}},
        status="live",
        seed=1,
        market_type="cfamm",
        source="streaming",
        simulation_id="fees-flip",
        current_round=2,
        summary={},
    )
    pg_store.save_run_artifacts(
        "fees-flip",
        result={"fee_history": _TWO_DESTINATIONS_FEE_HISTORY},
        events=[],
        round_snapshots=[],
    )
    # Still live → still no rows.
    assert pg_store.query_fee_history("fees-flip") == []
    pg_store.update_run("fees-flip", status="completed")
    assert pg_store.query_fee_history("fees-flip") == _TWO_DESTINATIONS_FEE_HISTORY


def test_materialise_fees_is_idempotent(pg_store):
    """Re-saving the result must not duplicate rows. The aggregation is
    DELETE-then-INSERT; the PK ``(run_id, round, destination, token_id)``
    catches any future regression that drops the DELETE."""
    _create_completed_run(
        pg_store,
        "fees-3",
        result={"fee_history": _TWO_DESTINATIONS_FEE_HISTORY},
    )
    # Second save with the same payload (engine retries, replay, etc.).
    pg_store.save_run_artifacts(
        "fees-3",
        result={"fee_history": _TWO_DESTINATIONS_FEE_HISTORY},
        events=[],
        round_snapshots=[],
    )
    assert pg_store.query_fee_history("fees-3") == _TWO_DESTINATIONS_FEE_HISTORY


def test_materialise_fees_handles_revised_history(pg_store):
    """If the second save carries a different fee_history (e.g. a longer
    replay), the table must reflect the new state — not the union."""
    _create_completed_run(
        pg_store,
        "fees-4",
        result={"fee_history": [{"lp": {"SOL": 1.0}}]},
    )
    assert pg_store.query_fee_history("fees-4") == [{"lp": {"SOL": 1.0}}]
    pg_store.save_run_artifacts(
        "fees-4",
        result={"fee_history": [{"lp": {"USDC": 99.0}}, {"protocol": {"USDC": 1.0}}]},
        events=[],
        round_snapshots=[],
    )
    assert pg_store.query_fee_history("fees-4") == [
        {"lp": {"USDC": 99.0}},
        {"protocol": {"USDC": 1.0}},
    ]


def test_query_fee_history_preserves_trailing_empty_rounds(pg_store):
    """Chart consumers iterate ``fee_history`` by index alongside
    ``price_history``; a run whose final rounds happen to have no fees
    must still see the trailing empty dicts. Bounding the reconstructed
    list on ``MAX(round)`` from the ``fees`` table (the earlier draft)
    would truncate those rounds and decouple the two histories' lengths.
    """
    fee_history = [
        {},                                    # round 0
        {"lp": {"SOL": 1.5}},                  # round 1
        {},                                    # round 2 — interior empty (covered)
        {},                                    # round 3 — trailing empty (regression)
        {},                                    # round 4 — trailing empty (regression)
    ]
    _create_completed_run(
        pg_store,
        "fees-trailing",
        result={"fee_history": fee_history},
    )
    reconstructed = pg_store.query_fee_history("fees-trailing")
    assert reconstructed == fee_history
    assert len(reconstructed) == 5


def test_materialise_fees_handles_bigint_marker_amounts(pg_store):
    """``simulation_result_to_dict`` wraps Python ints above the JS safe
    integer range as ``{'__defi_sim_bigint__': '<digits>'}`` (see
    ``engine/json.py:62-63``). Token base units (lamports, gwei, etc.)
    routinely exceed that, so realistic Solana / EVM-style runs put bigint
    markers in ``result.fee_history``. The materialiser must unwrap them
    before binding to NUMERIC — earlier draft fell through and psycopg
    blew up on the dict bind.
    """
    from defi_sim.engine.json import BIGINT_MARKER

    big_lamports = (1 << 53) + 17  # one past the JS safe range, deterministic
    fee_history = [
        {},
        {"lp": {"SOL": {BIGINT_MARKER: str(big_lamports)}}},
    ]
    _create_completed_run(pg_store, "fees-bigint", result={"fee_history": fee_history})
    reconstructed = pg_store.query_fee_history("fees-bigint")
    assert len(reconstructed) == 2
    # NUMERIC round-trips bigints exactly; query_fee_history floats them on
    # the wire (matching the legacy ``result.fee_history`` shape), and
    # ``float(int)`` is lossless for our magnitude here.
    assert reconstructed[1]["lp"]["SOL"] == float(big_lamports)


def test_read_overview_typed_slices_returns_replay_diff(pg_store):
    """``replay_diff`` is written at ``result['replay_diff']`` by
    ``persist_replay_run`` (``runtime.py:128-129``) and read from there by
    ``calibrationBands.ts:113``. Phase 5.1 split it onto its own
    ``runs.replay_diff`` typed column; the view bundle reads from there.
    Earlier drafts of the JSONB-pluck looked under
    ``result['metadata']['replay_diff']`` and silently returned ``null``
    for every replay run — this test pins the top-level source.
    """
    band = {"per_metric_error": {"price": {"abs_pct_p50": 0.1}}}
    _create_completed_run(
        pg_store,
        "replay-1",
        result={"fee_history": [], "replay_diff": band},
    )
    slices = pg_store.read_overview_typed_slices("replay-1")
    assert slices["replay_diff"] == band
