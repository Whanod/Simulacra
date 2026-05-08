"""Shareable run-link endpoint tests (PRD US-009 line 1192)."""

from __future__ import annotations

from datetime import datetime, timedelta

from defi_sim_api.routers import share as share_router

from tests.api.conftest import CFAMM_SPEC

CORPUS_SLOT = 160_000_001


def test_run_link_resolves_to_results(client) -> None:
    run_resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["run_id"]

    resolve_resp = client.get(f"/share/runs/{run_id}")
    assert resolve_resp.status_code == 200, resolve_resp.text
    body = resolve_resp.json()
    assert body["run_id"] == run_id
    assert body["page_path"] == f"/r/{run_id}"
    assert body["results_path"] == f"/results/{run_id}"
    assert body["permanent"] is False
    assert body["expires_at"] is not None
    assert body["run"]["run_id"] == run_id
    assert body["run"]["spec"]["seed"] == CFAMM_SPEC["seed"]
    assert body["spec"]["seed"] == CFAMM_SPEC["seed"]
    assert body["result"]["num_rounds_executed"] == CFAMM_SPEC["num_rounds"]

    redirect_resp = client.get(f"/r/{run_id}", follow_redirects=False)
    assert redirect_resp.status_code == 303, redirect_resp.text
    assert redirect_resp.headers["location"] == f"/results/{run_id}"


def test_corpus_replay_run_link_is_permanent(client) -> None:
    replay_resp = client.post(
        "/v1/replay",
        json={"slot_range": [CORPUS_SLOT, CORPUS_SLOT], "counterfactuals": []},
    )
    assert replay_resp.status_code == 200, replay_resp.text
    run_id = replay_resp.json()["run_id"]

    resolve_resp = client.get(f"/share/runs/{run_id}")
    assert resolve_resp.status_code == 200, resolve_resp.text
    body = resolve_resp.json()
    assert body["run_id"] == run_id
    assert body["permanent"] is True
    assert body["expires_at"] is None


def test_ephemeral_run_expires_after_30_days(client, monkeypatch) -> None:
    run_resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["run_id"]

    created = client.get(f"/runs/{run_id}").json()["created_at"]
    created_at = datetime.fromisoformat(created)
    monkeypatch.setattr(
        share_router,
        "_utc_now",
        lambda: created_at + timedelta(days=31),
    )

    resolve_resp = client.get(f"/share/runs/{run_id}")
    assert resolve_resp.status_code == 410, resolve_resp.text
