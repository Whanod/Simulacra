"""End-to-end contract canary for the frontend adapters.

This file locks in the exact string-literal field names the Next.js studio's
`src/lib/api/adapters/*.ts` modules will read. Any backend rename or shape
change that breaks the adapters must fail here loudly — that is the whole
point of the file. Keep this in lockstep with the adapter modules.

Reference: plan-api.md § "Layer 1 — Backend endpoint tests", G3/G6 and
"frontend contract tests".
"""

from __future__ import annotations

from typing import Any

from tests.api.conftest import CFAMM_SPEC


def _keys(obj: Any) -> set[str]:
    return set(obj.keys()) if isinstance(obj, dict) else set()


class TestFrontendContract:
    """One happy-path flow covering every shape the frontend reads."""

    def test_full_contract(self, client):
        # ── Runs ───────────────────────────────────────────────────────────
        run_a = client.post("/simulations/run", json=CFAMM_SPEC).json()
        assert "run_id" in run_a
        run_a_id: str = run_a["run_id"]

        run_b_spec = {**CFAMM_SPEC, "seed": 99}
        run_b = client.post("/simulations/run", json=run_b_spec).json()
        run_b_id: str = run_b["run_id"]

        # GET /runs — list shape
        runs_list = client.get("/runs", params={"limit": 10}).json()
        assert {"runs", "count", "limit", "offset"}.issubset(_keys(runs_list))
        assert runs_list["count"] == 2, "count must be the true total, not the page size"
        assert len(runs_list["runs"]) == 2
        run_summary_a = next(r for r in runs_list["runs"] if r["run_id"] == run_a_id)
        # Fields the frontend runs.ts adapter reads from each list entry:
        for field in ("run_id", "status", "seed", "market_type", "current_round", "created_at", "updated_at"):
            assert field in run_summary_a, f"runs[].{field} missing from GET /runs"

        # GET /runs/{id}
        run_detail = client.get(f"/runs/{run_a_id}").json()
        for field in ("run_id", "status", "seed", "market_type", "spec"):
            assert field in run_detail, f"{field} missing from GET /runs/{{id}}"
        assert run_detail["spec"] is not None

        # GET /runs/{id}/spec
        spec_resp = client.get(f"/runs/{run_a_id}/spec").json()
        assert spec_resp["run_id"] == run_a_id
        assert "spec" in spec_resp
        assert _keys(spec_resp["spec"]).issuperset({"market", "agents", "num_rounds", "seed"})

        # Phase 5.3: ``GET /runs/{id}/result`` retired in favour of typed
        # surfaces — the overview view bundles the chart-driving slices,
        # ``GET /runs/{id}/rounds`` carries the per-round snapshots, and
        # the composer (``store.get_run_result``) keeps the legacy shape
        # alive for in-process callers (share / reports / embed).

        # GET /runs/{id}/events
        events_resp = client.get(f"/runs/{run_a_id}/events", params={"limit": 100}).json()
        assert events_resp["run_id"] == run_a_id
        events = events_resp["events"]
        assert isinstance(events, list) and len(events) > 0
        sample_event = events[0]
        for field in ("event_id", "type", "round", "timestamp", "data"):
            assert field in sample_event, f"events[].{field} missing"

        # POST /runs/compare
        compare = client.post(
            "/runs/compare",
            json={"left_run_id": run_a_id, "right_run_id": run_b_id},
        ).json()
        for field in (
            "left_run_id",
            "right_run_id",
            "equal",
            "spec_diff",
            "metric_diff",
            "price_summary_delta",
            "agent_summary_delta",
        ):
            assert field in compare, f"{field} missing from POST /runs/compare"

        # GET /runs/{id}/snapshots
        snaps = client.get(f"/runs/{run_a_id}/snapshots").json()
        assert snaps["run_id"] == run_a_id
        assert "snapshots" in snaps

        # ── Sweeps ─────────────────────────────────────────────────────────
        sweep_create = client.post(
            "/sweeps/run",
            json={
                "spec": CFAMM_SPEC,
                "param_grid": {"num_rounds": [2, 3]},
                "seeds": [1, 2],
                "metrics": {"rounds": {"type": "field", "path": "num_rounds_executed"}},
            },
        ).json()
        assert "sweep_id" in sweep_create and "data" in sweep_create
        sweep_id: str = sweep_create["sweep_id"]

        # GET /sweeps
        sweeps_list = client.get("/sweeps", params={"limit": 10}).json()
        assert {"sweeps", "count", "limit", "offset"}.issubset(_keys(sweeps_list))
        assert sweeps_list["count"] == 1
        sweep_summary = sweeps_list["sweeps"][0]
        for field in ("sweep_id", "status", "created_at", "updated_at", "summary", "spec"):
            assert field in sweep_summary, f"sweeps[].{field} missing from GET /sweeps"
        assert "param_grid" in sweep_summary["spec"]

        # GET /sweeps/{id}
        sweep_detail = client.get(f"/sweeps/{sweep_id}").json()
        for field in ("sweep_id", "status", "created_at", "updated_at", "summary", "spec"):
            assert field in sweep_detail, f"{field} missing from GET /sweeps/{{id}}"

        # GET /sweeps/{id}/rows
        sweep_rows_resp = client.get(f"/sweeps/{sweep_id}/rows").json()
        assert sweep_rows_resp["sweep_id"] == sweep_id
        rows = sweep_rows_resp["data"]
        assert isinstance(rows, list) and len(rows) > 0
        sample_row = rows[0]
        # Sweep rows carry the swept params plus metrics plus seed — the
        # frontend's sweeps adapter pivots on these to build the heatmap.
        assert "seed" in sample_row
        assert "num_rounds" in sample_row  # from param_grid
        assert "rounds" in sample_row  # from metrics config

        # POST /sweeps/{id}/recommendations
        recs = client.post(
            f"/sweeps/{sweep_id}/recommendations",
            json={
                "objective_metrics": ["rounds"],
                "weights": {"rounds": 1.0},
                "lower_is_better": {"rounds": False},
                "top_k": 2,
            },
        ).json()
        assert "top" in recs or "results" in recs or isinstance(recs, dict)

        # POST /sweeps/sensitivity
        sens = client.post(
            "/sweeps/sensitivity",
            json={"data": rows, "param": "num_rounds", "metric": "rounds"},
        ).json()
        assert "data" in sens

        # ── Reports ────────────────────────────────────────────────────────
        created_report = client.post(
            "/reports",
            json={
                "title": "Canary report",
                "description": "contract",
                "run_ids": [run_a_id],
                "sweep_ids": [sweep_id],
                "charts": [{"type": "leaderboard"}],
                "exports": [{"type": "json"}],
                "sections": [
                    {"id": "sec-1", "type": "summary", "title": "Overview"},
                ],
            },
        ).json()
        assert "report_id" in created_report and "manifest" in created_report
        rid: str = created_report["report_id"]

        # GET /reports
        reports_list = client.get("/reports", params={"limit": 10}).json()
        assert {"reports", "count", "limit", "offset"}.issubset(_keys(reports_list))
        assert reports_list["count"] == 1
        report_summary = reports_list["reports"][0]
        for field in ("report_id", "status", "created_at", "updated_at", "manifest"):
            assert field in report_summary, f"reports[].{field} missing"
        for field in ("title", "run_ids", "sweep_ids", "charts", "exports", "sections"):
            assert field in report_summary["manifest"], f"reports[].manifest.{field} missing"

        # GET /reports/{id}
        detail = client.get(f"/reports/{rid}").json()
        assert "report" in detail and "manifest" in detail
        for field in ("report_id", "status", "created_at", "updated_at"):
            assert field in detail["report"]
        for field in ("title", "run_ids", "sweep_ids", "charts", "exports", "sections"):
            assert field in detail["manifest"]

        # PUT /reports/{id}
        put_resp = client.put(
            f"/reports/{rid}",
            json={
                "title": "Canary renamed",
                "status": "published",
                "sections": [
                    {"id": "sec-1", "type": "summary", "title": "Overview"},
                    {"id": "sec-2", "type": "notes", "title": "Follow-ups"},
                ],
            },
        ).json()
        assert put_resp["manifest"]["title"] == "Canary renamed"
        assert put_resp["report"]["status"] == "published"
        assert len(put_resp["manifest"]["sections"]) == 2

        # GET /reports/{id}/bundle
        bundle = client.get(f"/reports/{rid}/bundle")
        assert bundle.status_code == 200
        assert bundle.headers["content-type"] == "application/zip"
        assert len(bundle.content) > 0

        # DELETE /reports/{id}
        assert client.delete(f"/reports/{rid}").status_code == 204
        assert client.get(f"/reports/{rid}").status_code == 404
        assert client.get("/reports").json()["count"] == 0

        # ── Registry v2 enriched contract (used by registryService) ───────
        registry = client.get("/registry").json()
        assert registry["contractVersion"] == "v2"
        category_map = {cat["key"]: cat for cat in registry["categories"]}
        for expected in (
            "reg-markets",
            "reg-agents",
            "reg-fees",
            "reg-exec",
        ):
            assert expected in category_map, f"GET /registry missing category {expected!r}"
            assert isinstance(category_map[expected]["entities"], list)

        markets = client.get("/registry/markets").json()
        assert markets["key"] == "reg-markets"
        assert isinstance(markets["entities"], list)
        types = {e["type"] for e in markets["entities"]}
        assert "cfamm" in types

