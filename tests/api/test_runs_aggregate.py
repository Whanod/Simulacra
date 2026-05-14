"""HTTP coverage for the Phase 3 multi-run aggregation endpoint.

``POST /runs/aggregate`` is the SQL-aggregation sibling of the legacy
``/runs/compare`` (pairwise diff). The Postgres-only SQL path is verified
through ``tests.api.test_postgres_store.test_aggregate_round_metrics_*``;
this file exercises the HTTP contract against the default backend.
"""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_aggregate_requires_run_ids(client):
    resp = client.post("/runs/aggregate", json={"metric": "volume"})
    assert resp.status_code == 422


def test_aggregate_requires_metric(client):
    resp = client.post("/runs/aggregate", json={"run_ids": ["a"]})
    assert resp.status_code == 422


def test_aggregate_rejects_unknown_metric(client):
    run_id = _create_run(client)
    resp = client.post(
        "/runs/aggregate", json={"run_ids": [run_id], "metric": "not_a_metric"}
    )
    assert resp.status_code == 404


def test_aggregate_returns_one_row_per_run(client):
    a = _create_run(client)
    b = _create_run(client)
    body = client.post(
        "/runs/aggregate", json={"run_ids": [a, b], "metric": "num_actions"}
    ).json()
    assert body["metric"] == "num_actions"
    assert body["agent_id"] is None
    assert [r["run_id"] for r in body["runs"]] == [a, b]
    for row in body["runs"]:
        assert set(row) == {"run_id", "total", "final_round"}


def test_aggregate_unknown_run_returns_null_total(client):
    """The plan calls for missing runs to surface as nulls rather than 404 —
    a cross-run report shouldn't fail wholesale because one input vanished."""
    run_id = _create_run(client)
    body = client.post(
        "/runs/aggregate",
        json={"run_ids": [run_id, "no-such-run"], "metric": "num_actions"},
    ).json()
    rows = {r["run_id"]: r for r in body["runs"]}
    assert rows["no-such-run"]["total"] is None
    assert rows["no-such-run"]["final_round"] is None
