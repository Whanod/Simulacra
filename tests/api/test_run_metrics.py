"""Phase 3 metrics endpoint coverage.

Exercises ``GET /runs/{id}/metrics/{metric}`` against the Postgres-backed
``client`` fixture. The store-level contract is covered separately by
``test_postgres_store.test_query_round_metrics_*``.
"""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_metric_unknown_returns_404(client):
    run_id = _create_run(client)
    resp = client.get(f"/runs/{run_id}/metrics/this_is_not_a_metric")
    assert resp.status_code == 404
    assert "not exposed" in resp.json()["detail"]


def test_metric_volume_rollup_shape(client):
    run_id = _create_run(client)
    body = client.get(f"/runs/{run_id}/metrics/volume").json()
    assert body["run_id"] == run_id
    assert body["metric"] == "volume"
    assert body["agent_id"] is None
    series = body["series"]
    assert isinstance(series, list)
    for entry in series:
        assert set(entry) == {"round", "value"}
        assert isinstance(entry["round"], int)


def test_metric_unknown_run_returns_404(client):
    resp = client.get("/runs/no-such-run/metrics/volume")
    assert resp.status_code == 404


def test_metric_round_range(client):
    run_id = _create_run(client)
    # CFAMM_SPEC has 5 rounds; from=2&to=3 must yield rows within that window.
    body = client.get(
        f"/runs/{run_id}/metrics/num_actions", params={"from": 2, "to": 3}
    ).json()
    rounds = [entry["round"] for entry in body["series"]]
    assert all(2 <= r <= 3 for r in rounds)
