"""Embeddable chart endpoint tests (PRD US-009 embed widget)."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def test_embed_endpoint_returns_self_contained_html(client) -> None:
    run_resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["run_id"]

    resp = client.get(f"/embed/cumulative-volume?run={run_id}")

    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    assert "frame-ancestors" in resp.headers["content-security-policy"]
    body = resp.text
    assert f'data-run-id="{run_id}"' in body
    assert 'data-chart-id="cumulative-volume"' in body
    assert "<svg" in body
    assert "<script" not in body.lower()
    assert "fetch(" not in body


def test_embed_endpoint_rejects_unknown_chart(client) -> None:
    run_resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["run_id"]

    resp = client.get(f"/embed/not-a-chart?run={run_id}")

    assert resp.status_code == 404, resp.text
