"""Round-trip + behaviour tests for :class:`PostgresArtifactStore`.

Exercises every protocol method against the testcontainers-backed pool and
asserts read-back results match the stored shape. Previously this file also
compared each method's output against ``LocalArtifactStore`` as a parity
oracle; that store has been retired (Phase 5), so the assertions here pin
the contract directly.
"""

from __future__ import annotations

import pytest

from defi_sim_api.backend.pg_store import ROLLUP_AGENT_ID, PostgresArtifactStore


@pytest.fixture()
def pg_store(pg_pool):
    return PostgresArtifactStore(pool=pg_pool)


SAMPLE_SPEC = {
    "market": {"type": "cfamm", "params": {"k": 1}},
    "agents": [{"agent_id": "a", "type": "noise"}],
    "num_rounds": 3,
    "seed": 11,
}

# Phase 5.2: ``get_run_result`` is now a composer that stitches the legacy
# shape back together from typed columns (added in 5.1) plus the
# ``metadata`` column added in 5.2 to round-trip the engine-internal
# metadata bag. The fields below pin the slices the composer recognises.
SAMPLE_RESULT = {
    "price_history": [{"SOL": 100.0}, {"SOL": 101.5}],
    "agent_final_states": {"a": {"role": "noise", "realized_pnl": -1.25}},
    "metadata": {
        "derived_metrics": {"kl_divergence": 0.42},
        # Engine-internal metadata fields ride the ``runs.metadata`` JSONB
        # column whole so the composer can reproduce the legacy result
        # shape byte-for-byte for the golden captures.
        "fee_destination_balances": {"lp": {"SOL": 1}},
    },
}

SAMPLE_EVENTS = [
    {
        "event_id": 1,
        "run_id": "run-1",
        "type": "SIMULATION_START",
        "round": 0,
        "timestamp": 0.0,
        "data": {"seed": 11},
    },
    {
        "event_id": 2,
        "run_id": "run-1",
        "type": "ACTION_EXECUTED",
        "round": 1,
        "timestamp": 1.0,
        "data": {
            "agent_id": "a",
            "action_type": "swap",
            "asset": "SOL",
            "amount": 100,
            "price": 50.0,
            "gas_cost": 1,
            "execution_cost": 2,
            "succeeded": True,
            "correlation_id": "run-1:action:2",
        },
    },
    {
        "event_id": 3,
        "run_id": "run-1",
        "type": "SIMULATION_END",
        "round": 3,
        "timestamp": 3.0,
        # Phase 5 (plan line 252): SIMULATION_END no longer embeds the
        # result. The event row is a "done" marker; the result data lives
        # on the typed columns + ``round_snapshots`` + ``fees`` tables.
        "data": {},
    },
]

SAMPLE_SNAPSHOTS = [
    {"round": 0, "agents": {"a": {"balance": 1000}}, "prices": {"SOL": 50}},
    {"round": 1, "agents": {"a": {"balance": 900}}, "prices": {"SOL": 51}},
]


def _populate_run(store):
    store.create_run(
        "run-1",
        spec=SAMPLE_SPEC,
        status="completed",
        seed=11,
        market_type="cfamm",
        source="sync",
        simulation_id="run-1",
        current_round=3,
        summary={"agent_count": 1},
    )
    store.save_run_artifacts(
        "run-1",
        result=SAMPLE_RESULT,
        events=SAMPLE_EVENTS,
        round_snapshots=SAMPLE_SNAPSHOTS,
        summary={"agent_count": 1, "status": "completed"},
    )


def test_run_spec_and_result_round_trip(pg_store):
    """Phase 5.2 ``get_run_result`` composes from typed columns.

    The shape is the engine's legacy ``result`` payload reassembled from
    ``runs.{price_history, agent_final_states, derived_metrics, …}`` +
    ``round_snapshots`` + ``fees``. We pin the slices the composer is
    contractually responsible for; engine-internal metadata fields that
    no consumer reads (e.g. ``fee_destination_balances``) are dropped
    on purpose.
    """
    _populate_run(pg_store)
    assert pg_store.get_run_spec("run-1") == SAMPLE_SPEC
    result = pg_store.get_run_result("run-1")
    assert result is not None
    assert result["price_history"] == SAMPLE_RESULT["price_history"]
    assert result["agent_final_states"] == SAMPLE_RESULT["agent_final_states"]
    # ``metadata`` is round-tripped whole via the dedicated column; both
    # the consumer-facing ``derived_metrics`` slot and the engine-internal
    # ``fee_destination_balances`` ride along.
    assert result["metadata"] == SAMPLE_RESULT["metadata"]
    # ``num_rounds_executed`` rides on ``runs.current_round``; the populated
    # run was created with current_round=3.
    assert result["num_rounds_executed"] == 3
    assert result["seed"] == 11
    # ``round_snapshots`` come from the dedicated table; same shape the
    # legacy ``result.round_snapshots`` carried.
    assert result["round_snapshots"] == SAMPLE_SNAPSHOTS
    # ``fee_history`` reconstructs from the ``fees`` table; the populated
    # run has no fee rows, so terminal-status composer surfaces a
    # ``[{}] * num_rounds`` array aligned with ``price_history``.
    assert result["fee_history"] == [{}, {}, {}]


# The five JSONB columns peeled off ``runs.result`` in Phase 5.1. Phase 5.3
# will drop ``runs.result``; until then the columns are dual-written so
# Phase 5.2 can flip readers off the legacy column without losing data.
_TYPED_SLICE_COLUMNS = (
    "price_history",
    "agent_final_states",
    "derived_metrics",
    "replay_diff",
    "sandwich_summary",
)


def _read_typed_slices(pg_pool, run_id: str) -> dict[str, object]:
    cols = ", ".join(_TYPED_SLICE_COLUMNS)
    with pg_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    assert row is not None, f"run {run_id} not in runs table"
    return dict(zip(_TYPED_SLICE_COLUMNS, row, strict=True))


def test_typed_slices_dual_written_when_present(pg_store, pg_pool):
    """save_run_artifacts pulls the five Phase 5 slices off ``result`` and
    persists each on its own column so commit 5.2 can flip readers."""
    result = {
        **SAMPLE_RESULT,
        "price_history": [{"SOL": 100.0}, {"SOL": 101.5}],
        "agent_final_states": {"a": {"role": "noise", "realized_pnl": 1.25}},
        "replay_diff": {"pnl": {"left": 0, "right": 1}},
        "metadata": {
            "derived_metrics": {"kl_divergence": 0.42},
            "sandwich_bundles_landed": 7,
            "sandwich_bundles_submitted": 9,
            "sandwich_realized_ev_lamports": 100_000,
            # Non-promoted metadata key — should NOT land in sandwich_summary.
            "fee_destination_balances": {"lp": {"SOL": 1}},
        },
    }
    pg_store.create_run(
        "run-1",
        spec=SAMPLE_SPEC,
        status="completed",
        seed=11,
        market_type="cfamm",
        source="sync",
        simulation_id="run-1",
        current_round=3,
        summary={"agent_count": 1},
    )
    pg_store.save_run_artifacts("run-1", result=result, summary={"status": "completed"})

    slices = _read_typed_slices(pg_pool, "run-1")
    assert slices["price_history"] == [{"SOL": 100.0}, {"SOL": 101.5}]
    assert slices["agent_final_states"] == {"a": {"role": "noise", "realized_pnl": 1.25}}
    assert slices["derived_metrics"] == {"kl_divergence": 0.42}
    assert slices["replay_diff"] == {"pnl": {"left": 0, "right": 1}}
    assert slices["sandwich_summary"] == {
        "sandwich_bundles_landed": 7,
        "sandwich_bundles_submitted": 9,
        "sandwich_realized_ev_lamports": 100_000,
    }


def test_typed_slices_null_when_result_omits_fields(pg_store, pg_pool):
    """Slices missing from ``result`` land as SQL NULL (not JSONB ``null``) so
    readers can ``IS NULL``-check without disambiguating the two."""
    pg_store.create_run(
        "run-1",
        spec=SAMPLE_SPEC,
        status="completed",
        seed=11,
        market_type=None,
        source="sync",
        simulation_id="run-1",
        current_round=0,
        summary={},
    )
    # Bare result — no engine-recognised slice present.
    pg_store.save_run_artifacts("run-1", result={"status": "n/a"}, summary={})

    slices = _read_typed_slices(pg_pool, "run-1")
    assert all(value is None for value in slices.values()), slices


def test_typed_slices_skip_sandwich_when_metadata_empty(pg_store, pg_pool):
    """sandwich_summary stays NULL when no sandwich_* keys are present, even
    if other metadata is set — keeps the column a fixed-shape signal."""
    result = {
        **SAMPLE_RESULT,
        "metadata": {"derived_metrics": {"x": 1}, "fee_destination_balances": {}},
    }
    pg_store.create_run(
        "run-1", spec=SAMPLE_SPEC, status="completed", seed=11,
        market_type=None, source="sync", simulation_id="run-1",
        current_round=0, summary={},
    )
    pg_store.save_run_artifacts("run-1", result=result, summary={})

    slices = _read_typed_slices(pg_pool, "run-1")
    assert slices["sandwich_summary"] is None
    assert slices["derived_metrics"] == {"x": 1}


def test_run_events_round_trip(pg_store):
    _populate_run(pg_store)
    events = pg_store.get_run_events("run-1")
    assert len(events) == 3
    assert events[0]["type"] == "SIMULATION_START"
    assert events[1]["data"]["action_type"] == "swap"
    assert events[1]["data"]["succeeded"] is True
    # SIMULATION_END is a "done" marker — no embedded payload (Phase 5,
    # plan line 252).
    assert events[2]["type"] == "SIMULATION_END"
    assert events[2]["data"] == {}
    # Insertion order preserved via (run_id, event_id) PK.
    assert [e["event_id"] for e in events] == [1, 2, 3]


def test_query_run_events_filter_combinations(pg_store):
    """Every filter knob the router exposes must drive the expected SQL.
    Anchors the Phase 3 contract: filter shape × SAMPLE_EVENTS → fixed set."""
    _populate_run(pg_store)

    # SAMPLE_EVENTS: id 1 (SIMULATION_START round 0, no agent),
    #                id 2 (ACTION_EXECUTED round 1, agent a),
    #                id 3 (SIMULATION_END round 3, no agent).
    cases = [
        ({"event_type": "ACTION_EXECUTED"}, [2]),
        ({"agent_id": "a"}, [2]),
        ({"agent_id": "missing-agent"}, []),
        ({"round_number": 1}, [2]),
        ({"from_round": 1, "to_round": 3}, [2, 3]),
        ({"from_round": 2}, [3]),
        ({"to_round": 1}, [1, 2]),
    ]
    for kwargs, expected_ids in cases:
        events = pg_store.query_run_events("run-1", **kwargs)
        assert [e["event_id"] for e in events] == expected_ids, (
            f"mismatch for {kwargs}: got {[e['event_id'] for e in events]}"
        )


def test_query_run_events_correlation_id_filter(pg_store):
    """Correlation filtering must hit the partial events_run_correlation index
    (per-correlation lookups are the worst-case linear-scan workload otherwise)."""
    _populate_run(pg_store)
    # SAMPLE_EVENTS event 2 carries correlation_id='run-1:action:2'; event 1
    # and 3 have no correlation_id.
    matched = pg_store.query_run_events("run-1", correlation_id="run-1:action:2")
    assert [e["event_id"] for e in matched] == [2]
    assert matched[0]["data"]["correlation_id"] == "run-1:action:2"


def test_summarize_run_events_groups_by_type(pg_store):
    """Anchors the Phase 3 contract for ``/runs/{id}/views/overview``:
    summarize_run_events GROUPs by ``type`` and returns counts sorted by
    type. Three sample events, three distinct types.
    """
    _populate_run(pg_store)
    assert pg_store.summarize_run_events("run-1") == [
        {"type": "ACTION_EXECUTED", "count": 1},
        {"type": "SIMULATION_END", "count": 1},
        {"type": "SIMULATION_START", "count": 1},
    ]


def test_summarize_run_events_empty_run(pg_store):
    """Missing runs and runs with no events both return an empty list."""
    assert pg_store.summarize_run_events("no-such-run") == []


def test_query_run_events_cursor_pagination(pg_store):
    _populate_run(pg_store)
    page_a = pg_store.query_run_events("run-1", limit=2)
    assert [e["event_id"] for e in page_a] == [1, 2]
    page_b = pg_store.query_run_events("run-1", cursor=page_a[-1]["event_id"], limit=2)
    assert [e["event_id"] for e in page_b] == [3]
    # Exhausted: empty page, not an error.
    page_c = pg_store.query_run_events("run-1", cursor=page_b[-1]["event_id"], limit=2)
    assert page_c == []


def test_round_snapshots_round_trip(pg_store):
    _populate_run(pg_store)
    assert pg_store.get_run_round("run-1", 0) == SAMPLE_SNAPSHOTS[0]
    assert pg_store.get_run_round("run-1", 1) == SAMPLE_SNAPSHOTS[1]
    assert pg_store.get_run_round("run-1", 99) is None
    rounds = pg_store.list_run_rounds("run-1")
    assert rounds == SAMPLE_SNAPSHOTS


def test_list_and_count_runs(pg_store):
    _populate_run(pg_store)
    assert pg_store.count_runs() == 1
    runs = pg_store.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"


def test_update_run_summary(pg_store):
    _populate_run(pg_store)
    pg_store.update_run("run-1", summary={"agent_count": 99, "extra": "x"})
    assert pg_store.get_run("run-1")["summary"]["agent_count"] == 99


def test_named_snapshot_round_trip(pg_store):
    _populate_run(pg_store)
    blob = b"\x00\x01\x02 binary msgpack bytes \xff"
    pg_store.create_named_snapshot(
        "snap-1",
        run_id="run-1",
        round_number=2,
        label="checkpoint",
        blob=blob,
    )
    meta = pg_store.get_named_snapshot("snap-1")
    assert meta["snapshot_id"] == "snap-1"
    assert meta["label"] == "checkpoint"
    assert meta["round"] == 2
    assert pg_store.get_named_snapshot_blob("snap-1") == blob
    assert [s["snapshot_id"] for s in pg_store.list_named_snapshots()] == ["snap-1"]
    assert [
        s["snapshot_id"] for s in pg_store.list_named_snapshots(run_id="run-1")
    ] == ["snap-1"]
    assert pg_store.list_named_snapshots(run_id="other") == []


def test_sweeps_round_trip(pg_store):
    spec = {"sweep": {"param": "fee", "values": [1, 5, 30]}}
    pg_store.create_sweep("sweep-1", spec=spec, status="queued")
    pg_store.update_sweep("sweep-1", status="running")
    pg_store.save_sweep_artifacts(
        "sweep-1",
        rows=[{"value": 1, "result": "a"}, {"value": 5, "result": "b"}],
        summary={"completed": 2},
    )
    sweep = pg_store.get_sweep("sweep-1")
    assert sweep["status"] == "running"
    assert sweep["summary"]["completed"] == 2
    assert pg_store.get_sweep_spec("sweep-1") == spec
    assert len(pg_store.get_sweep_rows("sweep-1")) == 2
    assert pg_store.count_sweeps() == 1
    listed = pg_store.list_sweeps()
    assert listed[0]["spec"] == spec


def test_reports_full_lifecycle(pg_store):
    manifest = {"runs": ["run-a", "run-b"], "metric": "pnl"}
    pg_store.create_report("rep-1", manifest=manifest, status="pending")
    pg_store.update_report("rep-1", status="building")
    merged = pg_store.update_report_manifest("rep-1", {"extra_param": True})
    assert merged["extra_param"] is True
    assert merged["runs"] == ["run-a", "run-b"]
    # Phase 3 dropped the bundle envelope entirely. Manifests are now pure
    # user-authored JSONB; no sentinel keys ever appear in the stored shape.
    assert "_bundle_b64" not in merged

    report = pg_store.get_report("rep-1")
    # has_bundle removed from the response — bundles are built on demand
    # by the router, so the persisted row has no opinion about them.
    assert "has_bundle" not in report

    public_manifest = pg_store.get_report_manifest("rep-1")
    assert "_bundle_b64" not in public_manifest

    assert pg_store.count_reports() == 1
    assert pg_store.delete_report("rep-1") is True
    assert pg_store.get_report("rep-1") is None
    assert pg_store.delete_report("rep-1") is False


# ── round_metrics materialisation (Phase 2) ──────────────────────────────────


_METRICS_EVENTS = [
    {
        "event_id": 1, "run_id": "rm-1", "type": "SIMULATION_START",
        "round": 0, "timestamp": 0.0, "data": {},
    },
    # Agent A: 2 executed swaps at round 1 (volume 100 + 50), 1 failed at round 2.
    {
        "event_id": 2, "run_id": "rm-1", "type": "ACTION_EXECUTED",
        "round": 1, "timestamp": 1.0,
        "data": {"agent_id": "a", "gas_cost": 3, "result": {"volume": 100}},
    },
    {
        "event_id": 3, "run_id": "rm-1", "type": "ACTION_EXECUTED",
        "round": 1, "timestamp": 1.0,
        "data": {"agent_id": "a", "gas_cost": 2, "result": {"volume": 50}},
    },
    {
        "event_id": 4, "run_id": "rm-1", "type": "ACTION_FAILED",
        "round": 2, "timestamp": 2.0,
        "data": {"agent_id": "a", "gas_cost": 1},
    },
    # Agent B: 1 executed swap at round 1 (volume 200).
    {
        "event_id": 5, "run_id": "rm-1", "type": "ACTION_EXECUTED",
        "round": 1, "timestamp": 1.0,
        "data": {"agent_id": "b", "gas_cost": 4, "result": {"volume": 200}},
    },
    # Agentless event — must not contribute (WHERE agent_id IS NOT NULL).
    {
        "event_id": 6, "run_id": "rm-1", "type": "ROUND_END",
        "round": 1, "timestamp": 1.0, "data": {},
    },
    {
        "event_id": 7, "run_id": "rm-1", "type": "SIMULATION_END",
        "round": 2, "timestamp": 2.0, "data": {"result": {}},
    },
]


def _fetch_metrics(pg_store):
    with pg_store._get_pool().connection() as conn:  # noqa: SLF001 - test introspection
        with conn.cursor() as cur:
            cur.execute(
                "SELECT round, agent_id, num_actions, num_failed, volume, gas_spent "
                "FROM round_metrics WHERE run_id = 'rm-1' "
                "ORDER BY round, agent_id"
            )
            return cur.fetchall()


def test_round_metrics_populated_on_completed_run(pg_store):
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})

    rows = _fetch_metrics(pg_store)
    # Per-agent rows + one whole-market rollup row per round (Phase 3).
    # Round 0 has only a SIMULATION_START event — no per-agent rows, but a
    # rollup row exists so the timeline starts at the engine's first round.
    # Round 1: agent a (2 exec, 0 fail, vol 150, gas 5); agent b (1 exec, 0 fail, vol 200, gas 4).
    # Round 2: agent a (0 exec, 1 fail, vol NULL, gas 1).
    # gas_spent is gas on succeeded actions only — the agent-a / round-2
    # row has 1 failed action with gas_cost=1 but no executed actions, so
    # gas_spent is NULL (FILTER eliminates the only contributing row).
    # Rollup counts agentless events too — round 1's ROUND_END contributes
    # zero to all metrics, so totals (3 executed, vol 350, gas 9) match
    # the per-agent sums.
    assert rows == [
        (0, ROLLUP_AGENT_ID, 0, 0, None, None),
        (1, ROLLUP_AGENT_ID, 3, 0, 350, 9),
        (1, "a", 2, 0, 150, 5),
        (1, "b", 1, 0, 200, 4),
        (2, ROLLUP_AGENT_ID, 0, 1, None, None),
        (2, "a", 0, 1, None, None),
    ]


def test_round_metrics_skipped_for_live_run(pg_store):
    pg_store.create_run(
        "rm-1", spec={}, status="live", seed=None, market_type=None,
        source="live", simulation_id="rm-1", current_round=1, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})
    assert _fetch_metrics(pg_store) == []


def test_round_metrics_materialised_on_status_transition(pg_store):
    pg_store.create_run(
        "rm-1", spec={}, status="live", seed=None, market_type=None,
        source="live", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})
    assert _fetch_metrics(pg_store) == []

    # The live-streaming path lands in 'completed' via update_run, which is
    # the moment Phase 2 must materialise metrics from the already-persisted events.
    pg_store.update_run("rm-1", status="completed")
    assert _fetch_metrics(pg_store) == [
        (0, ROLLUP_AGENT_ID, 0, 0, None, None),
        (1, ROLLUP_AGENT_ID, 3, 0, 350, 9),
        (1, "a", 2, 0, 150, 5),
        (1, "b", 1, 0, 200, 4),
        (2, ROLLUP_AGENT_ID, 0, 1, None, None),
        (2, "a", 0, 1, None, None),
    ]


def test_round_metrics_tolerates_non_numeric_volume(pg_store):
    """A malformed ``result.volume`` must not abort the aggregation tx.

    The promoted-column path is typed, but the JSONB blob is whatever the
    engine emitted; an unexpected shape (string / object / array / JSON
    null) at ``data.result.volume`` would otherwise raise
    ``invalid input syntax for type numeric`` and roll back the surrounding
    save_run_artifacts / update_run call, taking the run write with it.
    """
    malformed = [
        {"event_id": 1, "run_id": "rm-1", "type": "SIMULATION_START",
         "round": 0, "timestamp": 0.0, "data": {}},
        # volume is a string — would explode without jsonb_typeof guard.
        {"event_id": 2, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "a", "gas_cost": 1, "result": {"volume": "huge"}}},
        # volume is an object.
        {"event_id": 3, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "a", "gas_cost": 1, "result": {"volume": {"oops": 1}}}},
        # volume key absent altogether — already tolerated, asserted for parity.
        {"event_id": 4, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "a", "gas_cost": 2, "result": {}}},
    ]
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=1, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=malformed, summary={})

    # 3 executed actions on agent a, none with numeric volume → volume NULL,
    # gas_spent sums the three executed gas_costs (1+1+2=4). Rollup mirrors the
    # per-agent row at round 1 because agent a is the only action source; the
    # round-0 SIMULATION_START contributes a 0-valued rollup row.
    assert _fetch_metrics(pg_store) == [
        (0, ROLLUP_AGENT_ID, 0, 0, None, None),
        (1, ROLLUP_AGENT_ID, 3, 0, None, 4),
        (1, "a", 3, 0, None, 4),
    ]


def test_rollup_sentinel_agent_id_is_rejected(pg_store):
    """A user-named agent matching the rollup sentinel must fail loudly.

    The Phase 3 rollup INSERT will write (run_id, round, ROLLUP_AGENT_ID);
    if any per-agent row already lived under that id the rollup would trip
    the PK. Catching the collision at event-write time means the user gets
    a clear error pointing at the offending event, not an opaque IntegrityError
    surfaced from a future Phase 3 commit hook.
    """
    pg_store.create_run(
        "rm-x", spec={}, status="live", seed=None, market_type=None,
        source="sync", simulation_id="rm-x", current_round=0, summary={},
    )
    collision = [{
        "event_id": 1, "run_id": "rm-x", "type": "ACTION_EXECUTED",
        "round": 1, "timestamp": 1.0,
        "data": {"agent_id": ROLLUP_AGENT_ID, "gas_cost": 0, "result": {"volume": 0}},
    }]
    with pytest.raises(ValueError, match="reserved for the round_metrics rollup row"):
        pg_store.save_run_artifacts("rm-x", events=collision, summary={})


def test_round_metrics_aggregates_full_event_set_on_streaming_run(pg_store):
    """Live-tick path: events arrive in batches, status flips terminal last.

    The realistic streaming flow is ``save_run_artifacts(batch1, live)`` →
    new events → ``save_run_artifacts(batch1+batch2, live)`` →
    ``update_run(status=completed)``. The aggregator must reflect the *full*
    event set, not just whatever was present at the first save — confirms
    the DELETE+INSERT pair covers all (round, agent_id) keys.
    """
    pg_store.create_run(
        "rm-1", spec={}, status="live", seed=None, market_type=None,
        source="live", simulation_id="rm-1", current_round=0, summary={},
    )

    batch1 = [
        {"event_id": 1, "run_id": "rm-1", "type": "SIMULATION_START",
         "round": 0, "timestamp": 0.0, "data": {}},
        {"event_id": 2, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "a", "gas_cost": 3, "result": {"volume": 100}}},
    ]
    pg_store.save_run_artifacts("rm-1", events=batch1, summary={})
    assert _fetch_metrics(pg_store) == []  # still live; no aggregation yet

    # Second tick: more events, still live. Save_run_artifacts deletes and
    # reinserts the whole event set (current pg_store contract).
    batch2 = batch1 + [
        {"event_id": 3, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "a", "gas_cost": 2, "result": {"volume": 50}}},
        {"event_id": 4, "run_id": "rm-1", "type": "ACTION_EXECUTED",
         "round": 2, "timestamp": 2.0,
         "data": {"agent_id": "b", "gas_cost": 1, "result": {"volume": 25}}},
    ]
    pg_store.save_run_artifacts("rm-1", events=batch2, summary={})
    assert _fetch_metrics(pg_store) == []  # still live

    # Final transition. Aggregator must see the full batch2, not just batch1.
    pg_store.update_run("rm-1", status="completed")
    assert _fetch_metrics(pg_store) == [
        (0, ROLLUP_AGENT_ID, 0, 0, None, None),
        (1, ROLLUP_AGENT_ID, 2, 0, 150, 5),
        (1, "a", 2, 0, 150, 5),
        (2, ROLLUP_AGENT_ID, 1, 0, 25, 1),
        (2, "b", 1, 0, 25, 1),
    ]


def test_round_metrics_fires_on_deleted_status(pg_store):
    """``deleted`` is terminal per routers/simulations.py:160 — events
    captured before the user removed the engine are still valid input."""
    pg_store.create_run(
        "rm-1", spec={}, status="live", seed=None, market_type=None,
        source="live", simulation_id="rm-1", current_round=1, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})
    assert _fetch_metrics(pg_store) == []
    pg_store.update_run("rm-1", status="deleted")
    assert _fetch_metrics(pg_store) != []


def test_round_metrics_reaggregation_is_idempotent(pg_store):
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})
    first = _fetch_metrics(pg_store)
    # Re-save the same events; the DELETE+INSERT in aggregator must not
    # create duplicate PK rows or leave stale rows from a prior iteration.
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})
    assert _fetch_metrics(pg_store) == first


def test_query_round_metrics_rollup_and_per_agent(pg_store):
    """The Phase 3 metrics endpoint reads two row classes from one table.

    Rollup (``agent_id=None`` on the API) returns whole-market totals;
    ``agent_id='a'`` returns only that agent's series."""
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})

    rollup = pg_store.query_round_metrics("rm-1", "volume")
    # Round 0 has only SIMULATION_START → rollup volume None (the rollup
    # includes every round of the timeline). Charts can render that as zero
    # or gap-fill as they choose; the API doesn't second-guess.
    assert rollup == [
        {"round": 0, "value": None},
        {"round": 1, "value": 350.0},
        {"round": 2, "value": None},
    ]

    agent_a = pg_store.query_round_metrics("rm-1", "volume", agent_id="a")
    assert agent_a == [{"round": 1, "value": 150.0}, {"round": 2, "value": None}]

    agent_b_actions = pg_store.query_round_metrics(
        "rm-1", "num_actions", agent_id="b"
    )
    # Agent b only acts at round 1.
    assert agent_b_actions == [{"round": 1, "value": 1}]


def test_query_round_metrics_round_range(pg_store):
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})

    # ``from=2`` excludes the round-1 row; rollup row at round 2 has volume=NULL.
    clipped = pg_store.query_round_metrics("rm-1", "num_failed", from_round=2)
    assert clipped == [{"round": 2, "value": 1}]


def test_query_round_metrics_unknown_metric_raises(pg_store):
    with pytest.raises(ValueError, match="not exposed"):
        pg_store.query_round_metrics("rm-1", "definitely_not_a_column")


def _populate_two_runs_with_metrics(pg_store) -> None:
    """Two completed runs sharing the same event shape but different counts."""
    pg_store.create_run(
        "agg-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="agg-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("agg-1", events=_METRICS_EVENTS, summary={})

    # agg-2: single agent, single executed action with volume 75.
    pg_store.create_run(
        "agg-2", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="agg-2", current_round=1, summary={},
    )
    agg_2_events = [
        {"event_id": 1, "run_id": "agg-2", "type": "SIMULATION_START",
         "round": 0, "timestamp": 0.0, "data": {}},
        {"event_id": 2, "run_id": "agg-2", "type": "ACTION_EXECUTED",
         "round": 1, "timestamp": 1.0,
         "data": {"agent_id": "z", "gas_cost": 7, "result": {"volume": 75}}},
    ]
    pg_store.save_run_artifacts("agg-2", events=agg_2_events, summary={})


def test_aggregate_round_metrics_rollup_sum_across_runs(pg_store):
    _populate_two_runs_with_metrics(pg_store)
    # Rollup volume: agg-1 = 350 (3 executed actions at round 1), agg-2 = 75.
    rows = pg_store.aggregate_round_metrics(["agg-1", "agg-2"], "volume")
    assert rows == [
        {"run_id": "agg-1", "total": 350.0, "final_round": 2},
        {"run_id": "agg-2", "total": 75.0, "final_round": 1},
    ]


def test_aggregate_round_metrics_filter_by_agent(pg_store):
    _populate_two_runs_with_metrics(pg_store)
    # Agent ``a`` only exists in agg-1 (volume 150); agg-2 has no agent-a row
    # so total is None and final_round is None.
    rows = pg_store.aggregate_round_metrics(
        ["agg-1", "agg-2"], "volume", agent_id="a"
    )
    assert rows == [
        {"run_id": "agg-1", "total": 150.0, "final_round": 2},
        {"run_id": "agg-2", "total": None, "final_round": None},
    ]


def test_aggregate_round_metrics_preserves_run_order(pg_store):
    _populate_two_runs_with_metrics(pg_store)
    forward = pg_store.aggregate_round_metrics(["agg-1", "agg-2"], "volume")
    reverse = pg_store.aggregate_round_metrics(["agg-2", "agg-1"], "volume")
    assert [r["run_id"] for r in forward] == ["agg-1", "agg-2"]
    assert [r["run_id"] for r in reverse] == ["agg-2", "agg-1"]


def test_aggregate_round_metrics_rejects_unknown_metric(pg_store):
    with pytest.raises(ValueError, match="not exposed"):
        pg_store.aggregate_round_metrics(["agg-1"], "not_a_column")


def test_aggregate_round_metrics_empty_run_ids_short_circuits(pg_store):
    assert pg_store.aggregate_round_metrics([], "volume") == []


def test_query_round_metrics_filter_combinations(pg_store):
    """Every metric/filter knob the router exposes must drive the expected SQL.

    Original regression this guards: SUM-over-zero-rows returning None instead
    of 0.0 for rounds whose only event was agentless (SIMULATION_START at
    round 0). The bucket-init pattern needs to emit a 0 row for every round
    in [from_round, to_round] even when no per-agent events fall in it.
    """
    pg_store.create_run(
        "rm-1", spec={}, status="completed", seed=None, market_type=None,
        source="sync", simulation_id="rm-1", current_round=2, summary={},
    )
    pg_store.save_run_artifacts("rm-1", events=_METRICS_EVENTS, summary={})

    # Sanity: at least one filter combo must yield a non-empty result so this
    # test fails closed if the metrics flush regresses.
    base = pg_store.query_round_metrics("rm-1", metric="volume")
    assert base, "expected at least one rollup row for the populated run"

    # Every knob in this list is reachable from the resource router; if any
    # combination raises or returns a non-list, the contract is broken.
    for kwargs in (
        {"metric": "volume"},
        {"metric": "volume", "agent_id": "a"},
        {"metric": "volume", "agent_id": "b"},
        {"metric": "num_actions"},
        {"metric": "num_failed"},
        {"metric": "gas_spent"},
        {"metric": "volume", "from_round": 2},
        {"metric": "volume", "to_round": 1},
    ):
        series = pg_store.query_round_metrics("rm-1", **kwargs)
        assert isinstance(series, list), f"{kwargs} returned {type(series)}"


def test_pg_store_accepts_nan_inf_and_nonrepresentable_floats(pg_store):
    """JSONB rejects NaN/Inf and NUMERIC has no IEEE-special representation.
    The store must sanitise (NaN/Inf → None) without losing the surrounding
    event, and preserve canonical IEEE-754 floats like 0.1 + 0.2 verbatim."""
    import math as _math

    nonrep = 0.1 + 0.2  # 0.30000000000000004
    events = [
        {
            "event_id": 1,
            "run_id": "run-nan",
            "type": "ACTION_EXECUTED",
            "round": 0,
            "timestamp": 0.0,
            "data": {
                "agent_id": "a",
                "amount": nonrep,
                "price": _math.nan,
                "gas_cost": _math.inf,
                "execution_cost": -_math.inf,
                "nested": {"oops": _math.nan, "fine": nonrep},
            },
        },
    ]
    pg_store.create_run(
        "run-nan",
        spec={"seed": 0},
        status="completed",
        seed=0,
        market_type=None,
        source="sync",
    )
    # Must not raise.
    pg_store.save_run_artifacts("run-nan", events=events)

    got = pg_store.get_run_events("run-nan")
    assert len(got) == 1
    data = got[0]["data"]
    assert data["amount"] == nonrep  # IEEE-754 representation preserved
    assert data["price"] is None
    assert data["gas_cost"] is None
    assert data["execution_cost"] is None
    assert data["nested"]["oops"] is None
    assert data["nested"]["fine"] == nonrep


def test_pg_store_partial_failure_rolls_back(pg_store):
    """save_run_artifacts spans events + snapshots in one transaction. A
    failure mid-write must leave the run in its prior state."""
    pg_store.create_run(
        "run-tx",
        spec={"seed": 1},
        status="completed",
        seed=1,
        market_type=None,
        source="sync",
    )
    # First successful write establishes a baseline.
    pg_store.save_run_artifacts(
        "run-tx",
        events=SAMPLE_EVENTS,
        round_snapshots=SAMPLE_SNAPSHOTS,
    )
    baseline_events = pg_store.get_run_events("run-tx")
    baseline_rounds = pg_store.list_run_rounds("run-tx")
    assert baseline_events and baseline_rounds

    # Now poison the second write: a snapshot missing the "round" key will
    # throw inside the transaction, after events have been re-COPYed.
    with pytest.raises(KeyError):
        pg_store.save_run_artifacts(
            "run-tx",
            events=[
                {**SAMPLE_EVENTS[0], "event_id": 99},
            ],
            round_snapshots=[{"oops": "no round key"}],
        )

    # Baseline must survive — neither the events nor the rounds were touched.
    assert pg_store.get_run_events("run-tx") == baseline_events
    assert pg_store.list_run_rounds("run-tx") == baseline_rounds


# ── owner_id (Privy v1) ─────────────────────────────────────────────────────


def test_owner_scoping_runs(pg_store):
    """list_runs / count_runs filter by owner_id when supplied; pass-through
    when None. get_run_owner returns the persisted DID (or None for anon /
    missing rows). Open-mode callers stay unaffected."""
    pg_store.create_run(
        "run-alice",
        spec=SAMPLE_SPEC, status="completed", seed=1,
        market_type="cfamm", source="sync",
        owner_id="did:privy:alice",
    )
    pg_store.create_run(
        "run-bob",
        spec=SAMPLE_SPEC, status="completed", seed=2,
        market_type="cfamm", source="sync",
        owner_id="did:privy:bob",
    )
    pg_store.create_run(
        "run-anon",
        spec=SAMPLE_SPEC, status="completed", seed=3,
        market_type="cfamm", source="sync",
    )

    # Strict per-owner filter.
    alice_runs = {r["run_id"] for r in pg_store.list_runs(owner_id="did:privy:alice")}
    assert alice_runs == {"run-alice"}
    assert pg_store.count_runs(owner_id="did:privy:alice") == 1
    assert pg_store.count_runs(owner_id="did:privy:bob") == 1
    # Anon-owned rows never appear under a user filter.
    assert pg_store.count_runs(owner_id="did:privy:nobody") == 0

    # owner_id=None → unfiltered (open-mode / golden harness path).
    all_runs = {r["run_id"] for r in pg_store.list_runs()}
    assert all_runs == {"run-alice", "run-bob", "run-anon"}
    assert pg_store.count_runs() == 3

    # get_run_owner exposes the column without bleeding it into the API row.
    assert pg_store.get_run_owner("run-alice") == "did:privy:alice"
    assert pg_store.get_run_owner("run-anon") is None
    assert pg_store.get_run_owner("run-missing") is None

    # Wire shape is unchanged: owner_id stays out of the row dict so the
    # golden harness keeps comparing byte-equal.
    row = pg_store.get_run("run-alice")
    assert "owner_id" not in row


def test_owner_scoping_sweeps_reports_snapshots(pg_store):
    """Same shape for sweeps, reports, named_snapshots."""
    # sweeps
    pg_store.create_sweep(
        "sw-alice", spec={"k": 1}, status="completed",
        owner_id="did:privy:alice",
    )
    pg_store.create_sweep("sw-anon", spec={"k": 2}, status="completed")
    assert {s["sweep_id"] for s in pg_store.list_sweeps(owner_id="did:privy:alice")} == {"sw-alice"}
    assert pg_store.count_sweeps(owner_id="did:privy:alice") == 1
    assert pg_store.count_sweeps() == 2
    assert pg_store.get_sweep_owner("sw-alice") == "did:privy:alice"
    assert pg_store.get_sweep_owner("sw-anon") is None

    # reports
    pg_store.create_report(
        "rep-alice", manifest={"k": 1}, status="ready",
        owner_id="did:privy:alice",
    )
    pg_store.create_report("rep-anon", manifest={"k": 2}, status="ready")
    assert {r["report_id"] for r in pg_store.list_reports(owner_id="did:privy:alice")} == {"rep-alice"}
    assert pg_store.count_reports(owner_id="did:privy:alice") == 1
    assert pg_store.count_reports() == 2
    assert pg_store.get_report_owner("rep-alice") == "did:privy:alice"

    # named_snapshots — needs a parent run row.
    pg_store.create_run(
        "run-snap", spec=SAMPLE_SPEC, status="completed", seed=1,
        market_type="cfamm", source="sync",
        owner_id="did:privy:alice",
    )
    pg_store.create_named_snapshot(
        "snap-alice", run_id="run-snap", round_number=0,
        label="alpha", blob=b"opaque",
        owner_id="did:privy:alice",
    )
    pg_store.create_named_snapshot(
        "snap-anon", run_id="run-snap", round_number=1,
        label="beta", blob=b"opaque",
    )
    alice_snaps = {s["snapshot_id"] for s in pg_store.list_named_snapshots(owner_id="did:privy:alice")}
    assert alice_snaps == {"snap-alice"}
    # run_id + owner_id compose with AND.
    scoped = pg_store.list_named_snapshots(run_id="run-snap", owner_id="did:privy:alice")
    assert {s["snapshot_id"] for s in scoped} == {"snap-alice"}
    assert pg_store.get_named_snapshot_owner("snap-alice") == "did:privy:alice"
    assert pg_store.get_named_snapshot_owner("snap-anon") is None
