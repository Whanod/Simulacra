"""Phase 3 ``/runs/{id}/views/overview`` coverage.

Per the migration plan exit criterion: tile resolution against at least two
templates. We exercise the base_spec of two distinct templates and assert
that each surfaces its own ``derived_metrics`` set as ``tiles``, and that
the rest of the bundle (``run``, ``spec_summary``, ``series``,
``event_summary``) is well-formed.

Runs against the Postgres-backed ``client`` fixture; the store-level
contract behind the view (``summarize_run_events``, ``query_round_metrics``)
is independently covered in ``test_postgres_store``.
"""

from __future__ import annotations

from defi_sim_api.backend.templates import find_template


_QUERYABLE_METRICS = ("volume", "num_actions", "num_failed", "gas_spent")


def _run_template(client, template_id: str, num_rounds: int = 5) -> str:
    template = find_template(template_id)
    assert template is not None, f"template {template_id!r} missing from catalog"
    # Shrink runtime to keep test fast; the view bundle shape is independent
    # of round count.
    spec = {**template["base_spec"], "num_rounds": num_rounds, "snapshot_interval": 1}
    resp = client.post("/simulations/run", json=spec)
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_overview_bundle_shape_for_whirlpool_template(client):
    run_id = _run_template(client, "whirlpool-fee-tuning")
    body = client.get(f"/runs/{run_id}/views/overview").json()

    # Phase 4.5 expanded the bundle to subsume chartDataFromResult /
    # metricsFromResult consumers; Phase 5.2 dropped the always-null
    # ``volume_history`` / ``liquidity_history`` fields (the engine
    # never populated them) and now reads every other slice off a typed
    # column on ``runs`` (price_history, agent_final_states, etc.) or
    # from ``round_snapshots`` (whirlpool, snapshot summaries). The
    # remaining shape is the page-rewire bundle the results page paints
    # off a single fetch.
    assert set(body) == {
        "run",
        "spec_summary",
        "tiles",
        "series",
        "event_summary",
        "price_history",
        "agent_final_states",
        "whirlpool_snapshots",
        "sandwich_summary",
        "replay_diff",
        "fee_history",
        "num_rounds_executed",
        "solana_slot_summary",
        "bundle_outcomes_summary",
        "jito_searcher_summary",
        "replay_metrics",
    }

    assert body["run"]["run_id"] == run_id
    assert body["spec_summary"]["market_type"] == "cfamm"
    assert body["spec_summary"]["agent_types"] == ["noise"]
    assert body["spec_summary"]["num_rounds"] == 5
    assert body["spec_summary"]["seed"] == 42

    # Tiles must be a flat str→number map (NaN dropped, Inf kept).
    assert isinstance(body["tiles"], dict)
    for key, value in body["tiles"].items():
        assert isinstance(key, str)
        assert isinstance(value, (int, float))
        # NaN check: only NaN is filtered, so no remaining value can be NaN.
        assert value == value  # noqa: PLR0124 — explicit NaN guard

    # Series must contain one bucket per queryable metric.
    assert set(body["series"]) == set(_QUERYABLE_METRICS)
    for metric, entries in body["series"].items():
        assert isinstance(entries, list)
        for entry in entries:
            assert set(entry) == {"round", "value"}, f"{metric}: {entry!r}"
            assert isinstance(entry["round"], int)

    # event_summary: list of {type, count} sorted by type.
    assert isinstance(body["event_summary"], list)
    types = [row["type"] for row in body["event_summary"]]
    assert types == sorted(types)
    assert all(isinstance(row["count"], int) and row["count"] >= 1 for row in body["event_summary"])
    # SIMULATION_END always fires once per run.
    end_rows = [row for row in body["event_summary"] if row["type"] == "SIMULATION_END"]
    assert end_rows == [{"type": "SIMULATION_END", "count": 1}]


def test_overview_bundle_shape_for_dlmm_template(client):
    # Second template — different market type, different agent mix — to lock
    # the per-template tile resolution Phase 3's exit criterion calls for.
    run_id = _run_template(client, "dlmm-bin-sustainability")
    body = client.get(f"/runs/{run_id}/views/overview").json()

    assert body["spec_summary"]["market_type"] == "cfamm"
    assert body["spec_summary"]["agent_types"] == ["passive_lp", "noise"]
    assert body["spec_summary"]["num_rounds"] == 5

    # The dlmm template exercises a passive LP, so the Whirlpool LP metrics
    # (range_il, fees_vs_il_breakeven, range_hit_fraction) may or may not
    # appear depending on the synthetic CFAMM scaffolding — assert only that
    # tiles is a well-formed dict and that any present numeric is finite or
    # +/-inf (NaN filtered).
    assert isinstance(body["tiles"], dict)
    for value in body["tiles"].values():
        assert isinstance(value, (int, float))
        assert value == value

    assert set(body["series"]) == set(_QUERYABLE_METRICS)


def test_overview_unknown_run_returns_404(client):
    resp = client.get("/runs/no-such-run/views/overview")
    assert resp.status_code == 404


def test_overview_tile_resolution_distinct_per_template(client):
    """Two templates with structurally different engines must surface
    structurally different tile sets.

    A single-market CFAMM template gets ``kl_divergence`` because the engine
    can identify a primary-token price series; a ``world`` template with two
    sub-markets has no single primary series, so ``kl_divergence`` is
    suppressed at the engine level. This is the real per-template divergence
    Phase 4 will key UI behaviour off — if the view ever flattened all
    templates to the same shape, this test catches it.
    """
    flat_id = _run_template(client, "whirlpool-fee-tuning")
    world_id = _run_template(client, "raydium-vs-whirlpool-arb")

    flat_tiles = client.get(f"/runs/{flat_id}/views/overview").json()["tiles"]
    world_tiles = client.get(f"/runs/{world_id}/views/overview").json()["tiles"]

    assert "kl_divergence" in flat_tiles
    assert "kl_divergence" not in world_tiles
    # Both still produce slippage — keeps the assertion above honest by
    # proving the divergence is structural, not "world template emits no
    # tiles at all".
    assert "slippage" in flat_tiles
    assert "slippage" in world_tiles


def test_overview_carries_chart_slices_from_result(client):
    """Phase 4.5: the view bundles the slices ``chartDataFromResult`` reads
    off the legacy ``result`` payload, so the results-page rewire can drive
    every chart from a single fetch. We don't pin numeric values here (the
    engine is the source of truth for those — golden suite owns that
    contract); we pin the structural surface so a future refactor can't
    silently drop a slice the frontend depends on.
    """
    run_id = _run_template(client, "whirlpool-fee-tuning")
    body = client.get(f"/runs/{run_id}/views/overview").json()

    # price_history, fee_history, agent_final_states are dataclass fields
    # on ``SimulationResult`` itself — the engine always emits them, so
    # absence here is a regression.
    assert isinstance(body["price_history"], list)
    assert isinstance(body["fee_history"], list)
    assert isinstance(body["agent_final_states"], dict)
    assert body["agent_final_states"]  # at least one agent

    # whirlpool_snapshots is populated only for whirlpool runs and absent
    # for plain CFAMM. The template name promises whirlpool tuning, but
    # the per-round whirlpool metrics block is only synthesised for
    # certain spec shapes; either a list or None is acceptable here.
    assert body["whirlpool_snapshots"] is None or isinstance(
        body["whirlpool_snapshots"], list
    )

    # Optional metadata passthroughs: present only when the engine emits
    # them. Both sandwich totals and replay_diff are template-specific.
    assert body["sandwich_summary"] is None or isinstance(body["sandwich_summary"], dict)
    assert body["replay_diff"] is None or isinstance(body["replay_diff"], dict)


def test_overview_carries_snapshot_summaries_from_round_snapshots(client):
    """Phase 4 page-rewire: the view must carry the four summaries the
    results page used to derive from ``result.round_snapshots``. The
    template here is non-Solana so we assert structure + ``None`` for the
    Solana-only fields; full numeric parity is covered by unit tests against
    the aggregation helpers.
    """
    run_id = _run_template(client, "whirlpool-fee-tuning")
    body = client.get(f"/runs/{run_id}/views/overview").json()

    # ``num_rounds_executed`` is on the engine's SimulationResult dataclass;
    # absence here would be a regression in the result serializer.
    assert isinstance(body["num_rounds_executed"], int)
    assert body["num_rounds_executed"] >= 1

    # Non-Solana template — neither slot ticker, bundle outcomes, nor
    # jito-searcher block fires. Pre-aggregation must surface that as
    # ``None`` so the client can hide the tiles without ad-hoc detection.
    assert body["solana_slot_summary"] is None
    assert body["bundle_outcomes_summary"] is None
    assert body["jito_searcher_summary"] is None
    assert body["replay_metrics"] is None


def test_overview_tiles_reject_non_numeric_values(client):
    """Tiles must be a strict str→number map. Booleans (which subclass ``int``
    in Python), strings, and None must all be filtered before the response
    leaves the handler — otherwise downstream consumers that assume numeric
    values silently break on truthy bools.
    """
    run_id = _run_template(client, "whirlpool-fee-tuning")
    tiles = client.get(f"/runs/{run_id}/views/overview").json()["tiles"]
    for value in tiles.values():
        assert not isinstance(value, bool), "boolean leaked into tiles"
        assert isinstance(value, (int, float))
        assert value == value  # noqa: PLR0124 — explicit NaN guard
