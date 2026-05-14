"""PRD story coverage for the productized backend API."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from defi_sim_api.backend.patches import PatchPathError, apply_spec_patch
from defi_sim_api.backend.pg_store import PostgresArtifactStore
from tests.api.conftest import CFAMM_SPEC, WORLD_SPEC


def test_us_001_repo_operating_rules_documented():
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "US-xxx" in agents or "US-004" in agents
    assert "Typecheck passes" in agents
    assert "pytest tests/api" in agents


def test_us_002_api_packaging_and_backend_bootstrap_documented():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    backend_docs = Path("docs/backend.md").read_text(encoding="utf-8")
    assert "src/defi_sim_api" in pyproject
    assert "pip install -e '.[api,dev]'" in backend_docs
    assert "uvicorn defi_sim_api.main:app --reload" in backend_docs


def test_us_003_artifact_store_roundtrip(pg_pool):
    store = PostgresArtifactStore(pool=pg_pool)

    store.create_run(
        "run-1",
        spec=CFAMM_SPEC,
        status="completed",
        seed=42,
        market_type="cfamm",
        source="sync",
        summary={"status": "completed"},
    )
    store.save_run_artifacts(
        "run-1",
        result={"result": "ok"},
        events=[{"event_id": 1, "type": "SIMULATION_END", "round": 1, "timestamp": 0.0}],
        round_snapshots=[{"round": 1, "agent_states": {}, "market_state": {}}],
        summary={"status": "completed", "available_rounds": [1]},
    )
    store.create_sweep("sweep-1", spec={"spec": True}, status="completed", summary={"row_count": 1})
    store.save_sweep_artifacts("sweep-1", rows=[{"seed": 1}], summary={"row_count": 1})
    store.create_report("report-1", manifest={"run_ids": ["run-1"]}, status="draft")
    store.create_named_snapshot("snap-1", run_id="run-1", round_number=1, label="checkpoint", blob=b"abc")

    assert store.get_run("run-1")["status"] == "completed"
    # Phase 5.2 ``get_run_result`` composes from typed columns rather than
    # echoing the literal ``result`` dict back. The save here doesn't
    # populate any engine-recognised slice, so the composed result is
    # the default skeleton (empty price_history / agent_final_states +
    # the round_snapshot row + the empty-summary metadata keys).
    composed = store.get_run_result("run-1")
    assert composed is not None
    assert composed["price_history"] == []
    assert composed["agent_final_states"] == {}
    assert composed["num_rounds_executed"] == 0
    assert store.get_run_events("run-1")[0]["event_id"] == 1
    assert store.get_run_round("run-1", 1)["round"] == 1
    assert store.get_sweep("sweep-1")["summary"]["row_count"] == 1
    assert store.get_sweep_rows("sweep-1")[0]["seed"] == 1
    assert store.get_report_manifest("report-1")["run_ids"] == ["run-1"]
    assert store.get_named_snapshot_blob("snap-1") == b"abc"


def test_us_004_sync_runs_persist_artifacts_and_return_run_id(client):
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    metadata = client.get(f"/runs/{run_id}").json()
    events = client.get(f"/runs/{run_id}/events").json()["events"]

    assert metadata["status"] == "completed"
    # Phase 5.3 dropped ``GET /runs/{id}/result``; ``num_rounds_executed`` now
    # rides on the run's metadata row (mirroring ``runs.current_round``).
    assert metadata["current_round"] == CFAMM_SPEC["num_rounds"]
    assert events[-1]["type"] == "SIMULATION_END"
    assert all("round" in event and "timestamp" in event for event in events)


def test_us_005_live_builds_persist_metadata_and_run_listing(client):
    build = client.post("/simulations/build", json=CFAMM_SPEC)
    assert build.status_code == 201
    run_id = build.json()["run_id"]

    metadata = client.get(f"/runs/{run_id}").json()
    listing = client.get("/runs").json()["runs"]

    assert metadata["run_id"] == run_id
    assert metadata["status"] == "live"
    assert metadata["seed"] == CFAMM_SPEC["seed"]
    assert metadata["market_type"] == "cfamm"
    assert any(item["run_id"] == run_id for item in listing)


def test_us_006_round_snapshot_queries_cover_single_and_world_runs(client):
    single_run = client.post("/simulations/run", json=CFAMM_SPEC).json()["run_id"]
    world_run = client.post("/simulations/run", json=WORLD_SPEC).json()["run_id"]

    single_rounds = client.get(f"/runs/{single_run}/rounds", params={"start": 2, "end": 4}).json()
    world_round = client.get(f"/runs/{world_run}/rounds/1").json()["snapshot"]

    assert single_rounds["available_rounds"] == [2, 3, 4]
    assert "agent_states" in single_rounds["snapshots"][0]
    assert world_round["all_market_states"] is not None
    assert world_round["agent_states"]


def test_us_007_named_snapshots_list_and_fork_live_engines(client):
    build = client.post("/simulations/build", json=CFAMM_SPEC).json()
    sim_id = build["simulation_id"]

    client.post(f"/simulations/{sim_id}/step")
    client.post(f"/simulations/{sim_id}/step")

    created = client.post(f"/simulations/{sim_id}/snapshots", json={"label": "midpoint"}).json()
    listed = client.get(f"/runs/{sim_id}/snapshots").json()["snapshots"]
    forked = client.post(f"/snapshots/{created['snapshot_id']}/fork").json()
    stepped = client.post(f"/simulations/{forked['simulation_id']}/step").json()

    assert created["round"] == 2
    assert listed[0]["snapshot_id"] == created["snapshot_id"]
    assert forked["current_round"] == 2
    assert stepped["round"] == 3


def test_us_008_run_comparison_supports_equal_and_different_pairs(client):
    left = client.post("/simulations/run", json=CFAMM_SPEC).json()["run_id"]
    right_same = client.post("/simulations/run", json=CFAMM_SPEC).json()["run_id"]
    different_spec = {**CFAMM_SPEC, "num_rounds": 3}
    right_diff = client.post("/simulations/run", json=different_spec).json()["run_id"]

    equal = client.post("/runs/compare", json={"left_run_id": left, "right_run_id": right_same}).json()
    different = client.post("/runs/compare", json={"left_run_id": left, "right_run_id": right_diff}).json()

    assert equal["equal"] is True
    assert different["equal"] is False
    assert "num_rounds" in different["metric_diff"]
    assert "num_rounds" in json.dumps(different["spec_diff"])


def test_us_009_agent_timelines_and_event_filters_work_for_live_and_persisted_runs(client):
    spec = {
        **CFAMM_SPEC,
        "agents": [
            {
                **CFAMM_SPEC["agents"][0],
                "params": {
                    **CFAMM_SPEC["agents"][0]["params"],
                    "frequency": 1.0,
                },
            }
        ],
    }
    build = client.post("/simulations/build", json=spec).json()
    sim_id = build["simulation_id"]
    client.put(f"/simulations/{sim_id}/parameters", json={"key": "fee_bps", "value": 55})
    client.post(f"/simulations/{sim_id}/step")
    client.post(f"/simulations/{sim_id}/step")

    live_timeline = client.get(f"/simulations/{sim_id}/agents/noise-1/timeline").json()["timeline"]
    live_events = client.get(
        f"/simulations/{sim_id}/events",
        params={"agent_id": "noise-1", "limit": 20},
    ).json()["events"]
    persisted_events = client.get(
        f"/runs/{sim_id}/events",
        params={"event_type": "PARAMETER_CHANGED"},
    ).json()["events"]

    assert live_timeline[0]["round"] == 1
    assert any(event["data"].get("agent_id") == "noise-1" for event in live_events)
    assert persisted_events[0]["data"]["key"] == "fee_bps"


def test_us_010_events_include_monotonic_ids_run_id_and_correlation_metadata(client):
    build = client.post("/simulations/build", json=CFAMM_SPEC).json()
    sim_id = build["simulation_id"]
    client.put(f"/simulations/{sim_id}/parameters", json={"key": "fee_bps", "value": 10})
    client.post(f"/simulations/{sim_id}/step")

    events = client.get(f"/simulations/{sim_id}/events").json()["events"]
    event_ids = [event["event_id"] for event in events]
    parameter_events = [event for event in events if event["type"] == "PARAMETER_CHANGED"]

    assert event_ids == sorted(event_ids)
    assert all(event["run_id"] == sim_id for event in events)
    assert parameter_events[0]["data"]["correlation_kind"] == "parameter_change"
    assert parameter_events[0]["data"]["correlation_id"]


def test_us_011_nested_spec_patch_helpers_accept_valid_and_reject_invalid_paths():
    patched = apply_spec_patch(CFAMM_SPEC, "market.params.initial_liquidity", 2_500_000)
    patched = apply_spec_patch(patched, "agents[agent_id=noise-1].params.frequency", 0.5)

    assert patched["market"]["params"]["initial_liquidity"] == 2_500_000
    assert patched["agents"][0]["params"]["frequency"] == 0.5

    with pytest.raises(PatchPathError):
        apply_spec_patch(CFAMM_SPEC, "agents[99].params.frequency", 0.1)


def test_us_012_sweeps_support_nested_patches_multi_seed_runs_and_metrics(client):
    resp = client.post(
        "/sweeps/run",
        json={
            "spec": CFAMM_SPEC,
            "param_grid": {
                "market.params.initial_liquidity": [1_000_000, 2_000_000],
                "agents[agent_id=noise-1].params.frequency": [0.0],
            },
            "seeds": [10, 20],
            "metrics": {
                "final_yes_price": {"type": "final_price", "token": "SOL"},
                "rounds": {"type": "field", "path": "num_rounds_executed"},
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    rows = body["data"]
    assert body["sweep_id"]
    assert len(rows) == 4
    assert all("final_yes_price" in row and "rounds" in row for row in rows)


def test_us_013_sweep_aggregates_include_stats_and_failure_rates(client):
    sweep = client.post(
        "/sweeps/run",
        json={
            "spec": CFAMM_SPEC,
            "param_grid": {"market.params.initial_liquidity": [1_000_000, 2_000_000]},
            "seeds": [1, 2],
            "metrics": {"rounds": {"type": "field", "path": "num_rounds_executed"}},
        },
    ).json()

    aggregates = client.post(
        f"/sweeps/{sweep['sweep_id']}/aggregates",
        json={"metric_columns": ["rounds"]},
    ).json()["data"]

    assert len(aggregates) == 2
    assert "rounds_mean" in aggregates[0]
    assert "rounds_std" in aggregates[0]
    assert "failure_rate" in aggregates[0]
    assert "invalid_rate" in aggregates[0]


def test_us_014_sweep_recommendations_rank_gate_and_suggest_next_experiment(client):
    sweep = client.post(
        "/sweeps/run",
        json={
            "spec": CFAMM_SPEC,
            "param_grid": {
                "market.params.initial_liquidity": [1_000_000, 2_000_000],
                "num_rounds": [2, 5],
            },
            "seeds": [1, 2],
            "metrics": {"rounds": {"type": "field", "path": "num_rounds_executed"}},
        },
    ).json()

    recommendation = client.post(
        f"/sweeps/{sweep['sweep_id']}/recommendations",
        json={
            "objective_metrics": ["rounds"],
            "weights": {"rounds": 1.0},
            "lower_is_better": {"rounds": False},
            "gate_conditions": {"min_rounds": {"column": "rounds", "op": ">=", "threshold": 3}},
            "top_k": 2,
        },
    ).json()

    assert recommendation["top_configurations"]
    assert recommendation["rejected_configurations"]
    assert recommendation["next_experiment"]["focus_parameter"] in {"market.params.initial_liquidity", "num_rounds"}


def test_us_015_template_catalog_returns_required_templates(client):
    resp = client.get("/templates/experiments")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    names = {template["name"] for template in templates}
    assert {
        "Whirlpool fee tuning",
        "Solana sandwich stress",
        "DLMM bin sustainability",
        "Raydium vs Whirlpool arbitrage",
    } <= names
    assert all(template["editable_fields"] for template in templates)
    assert all(template["recommended_metrics"] for template in templates)


def test_us_016_report_manifests_persist_and_bundle_selected_artifacts(client):
    run_id = client.post("/simulations/run", json=CFAMM_SPEC).json()["run_id"]
    sweep = client.post(
        "/sweeps/run",
        json={
            "spec": CFAMM_SPEC,
            "param_grid": {"num_rounds": [2]},
            "seeds": [1],
            "metrics": {"rounds": {"type": "field", "path": "num_rounds_executed"}},
        },
    ).json()

    created = client.post(
        "/reports",
        json={
            "title": "Durable report",
            "run_ids": [run_id],
            "sweep_ids": [sweep["sweep_id"]],
            "charts": [{"type": "leaderboard"}],
            "exports": [{"type": "json"}],
        },
    ).json()

    fetched = client.get(f"/reports/{created['report_id']}").json()
    bundle = client.get(f"/reports/{created['report_id']}/bundle")
    archive = zipfile.ZipFile(io.BytesIO(bundle.content))

    assert fetched["manifest"]["run_ids"] == [run_id]
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    assert "manifest.json" in archive.namelist()
    assert f"runs/{run_id}/result.json" in archive.namelist()
    assert f"sweeps/{sweep['sweep_id']}/rows.json" in archive.namelist()
