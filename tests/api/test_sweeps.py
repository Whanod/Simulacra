"""Sweep analysis endpoint tests."""

from __future__ import annotations


SWEEP_DATA = [
    {"fee_bps": 10, "seed": 1, "slippage": 0.01, "volume": 100},
    {"fee_bps": 10, "seed": 2, "slippage": 0.02, "volume": 110},
    {"fee_bps": 20, "seed": 1, "slippage": 0.03, "volume": 80},
    {"fee_bps": 20, "seed": 2, "slippage": 0.04, "volume": 90},
    {"fee_bps": 30, "seed": 1, "slippage": 0.05, "volume": 60},
    {"fee_bps": 30, "seed": 2, "slippage": 0.06, "volume": 70},
]


class TestRank:
    def test_ranks_by_composite_score(self, client):
        resp = client.post("/sweeps/rank", json={
            "data": SWEEP_DATA,
            "metric_columns": ["slippage", "volume"],
            "top_k": 2,
        })
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) <= 2
        assert "composite_score" in rows[0]

    def test_rank_with_weights(self, client):
        resp = client.post("/sweeps/rank", json={
            "data": SWEEP_DATA,
            "metric_columns": ["slippage", "volume"],
            "weights": {"slippage": 2.0, "volume": 1.0},
            "top_k": 3,
        })
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 3

    def test_rank_with_lower_is_better(self, client):
        resp = client.post("/sweeps/rank", json={
            "data": SWEEP_DATA,
            "metric_columns": ["slippage"],
            "lower_is_better": {"slippage": True},
            "top_k": 1,
        })
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 1


class TestSensitivity:
    def test_sensitivity_returns_stats(self, client):
        resp = client.post("/sweeps/sensitivity", json={
            "data": SWEEP_DATA,
            "param": "fee_bps",
            "metric": "slippage",
        })
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 3  # 3 distinct fee_bps values
        assert "mean" in rows[0]
        assert "std" in rows[0]
        assert "min" in rows[0]
        assert "max" in rows[0]


class TestSweepRun:
    def test_run_sweep_across_seeds(self, client):
        from tests.api.conftest import CFAMM_SPEC

        resp = client.post("/sweeps/run", json={
            "spec": CFAMM_SPEC,
            "param_grid": {"num_rounds": [2, 3]},
            "num_runs": 2,
            "master_seed": 7,
        })
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 4  # 2 param values x 2 seeds
        assert all("num_rounds_executed" in r for r in rows)
        assert all("seed" in r for r in rows)

    def test_run_sweep_with_explicit_seeds(self, client):
        from tests.api.conftest import CFAMM_SPEC

        resp = client.post("/sweeps/run", json={
            "spec": CFAMM_SPEC,
            "param_grid": {"num_rounds": [2]},
            "seeds": [10, 20],
        })
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert len(rows) == 2
        seeds = {r["seed"] for r in rows}
        assert seeds == {10, 20}


class TestListSweeps:
    """Coverage for GET /sweeps list endpoint (G2 in plan-api.md)."""

    def _create_sweep(self, client, tag: str) -> str:
        from tests.api.conftest import CFAMM_SPEC

        resp = client.post(
            "/sweeps/run",
            json={
                "spec": CFAMM_SPEC,
                "param_grid": {"num_rounds": [2]},
                "seeds": [1],
                "metrics": {f"rounds_{tag}": {"type": "field", "path": "num_rounds_executed"}},
            },
        )
        assert resp.status_code == 200
        return resp.json()["sweep_id"]

    def test_list_empty(self, client):
        resp = client.get("/sweeps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sweeps"] == []
        assert body["count"] == 0
        assert body["limit"] == 100
        assert body["offset"] == 0

    def test_list_after_create_contains_sweep(self, client):
        sweep_id = self._create_sweep(client, "a")
        resp = client.get("/sweeps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        ids = [item["sweep_id"] for item in body["sweeps"]]
        assert ids == [sweep_id]
        # Spec must be embedded so frontend list cards can render param names.
        entry = body["sweeps"][0]
        assert entry["spec"] is not None
        assert "param_grid" in entry["spec"]
        assert entry["status"] == "completed"
        assert "created_at" in entry
        assert "updated_at" in entry

    def test_list_newest_first(self, client):
        first = self._create_sweep(client, "one")
        second = self._create_sweep(client, "two")
        third = self._create_sweep(client, "three")
        body = client.get("/sweeps").json()
        ids = [item["sweep_id"] for item in body["sweeps"]]
        assert ids == [third, second, first]
        assert body["count"] == 3

    def test_list_pagination(self, client):
        sweep_ids = [self._create_sweep(client, f"p{i}") for i in range(5)]
        sweep_ids.reverse()  # newest-first expected order

        page1 = client.get("/sweeps", params={"limit": 2, "offset": 0}).json()
        assert [item["sweep_id"] for item in page1["sweeps"]] == sweep_ids[0:2]
        assert page1["count"] == 5
        assert page1["limit"] == 2
        assert page1["offset"] == 0

        page2 = client.get("/sweeps", params={"limit": 2, "offset": 2}).json()
        assert [item["sweep_id"] for item in page2["sweeps"]] == sweep_ids[2:4]
        assert page2["count"] == 5
        assert page2["offset"] == 2

        page3 = client.get("/sweeps", params={"limit": 2, "offset": 4}).json()
        assert [item["sweep_id"] for item in page3["sweeps"]] == sweep_ids[4:5]
        assert page3["count"] == 5

    def test_list_is_isolated_between_clients(self, client):
        """Sanity: the client fixture uses a fresh artifact root — list must be empty."""
        body = client.get("/sweeps").json()
        assert body["sweeps"] == []
        assert body["count"] == 0

    def test_get_sweep_now_includes_spec(self, client):
        """GET /sweeps/{id} is enriched with the stored spec for adapter use."""
        sweep_id = self._create_sweep(client, "spec")
        body = client.get(f"/sweeps/{sweep_id}").json()
        assert body["sweep_id"] == sweep_id
        assert body["spec"] is not None
        assert "param_grid" in body["spec"]


class TestSweepGate:
    def test_gate_passes(self, client):
        resp = client.post("/sweeps/gate", json={
            "data": SWEEP_DATA,
            "checks": {
                "slippage_ok": {"column": "slippage", "op": "<", "threshold": 0.1},
                "volume_ok": {"column": "volume", "op": ">", "threshold": 50},
            },
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is True
        assert body["results"]["slippage_ok"] is True
        assert body["results"]["volume_ok"] is True

    def test_gate_fails(self, client):
        resp = client.post("/sweeps/gate", json={
            "data": SWEEP_DATA,
            "checks": {
                "impossible": {"column": "slippage", "op": "<", "threshold": 0.005},
            },
        })
        assert resp.status_code == 200
        assert resp.json()["passed"] is False

    def test_gate_mean_check(self, client):
        resp = client.post("/sweeps/gate", json={
            "data": SWEEP_DATA,
            "checks": {
                "avg_volume": {"column": "volume", "op": "mean_>", "threshold": 50},
            },
        })
        assert resp.status_code == 200
        assert resp.json()["passed"] is True
