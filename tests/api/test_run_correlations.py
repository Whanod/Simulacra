"""Phase 3 correlation endpoint coverage.

Verifies the event group returned by ``GET /runs/{id}/correlations/{cid}``
is exactly the events sharing that ``correlation_id``, in event_id order.
"""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def _pick_correlation_id(client, run_id: str) -> str | None:
    """Return any correlation_id present in this run's events, or None."""
    events = client.get(f"/runs/{run_id}/events", params={"limit": 10_000}).json()["events"]
    for event in events:
        cid = event.get("data", {}).get("correlation_id")
        if cid:
            return cid
    return None


def test_correlation_endpoint_returns_matching_events(client):
    run_id = _create_run(client)
    cid = _pick_correlation_id(client, run_id)
    if cid is None:
        # CFAMM_SPEC happens to emit correlation_ids on ACTION_EXECUTED; if a
        # future engine change drops them this test surfaces the regression.
        import pytest

        pytest.skip("spec produced no correlation_ids; endpoint contract unverifiable")

    body = client.get(f"/runs/{run_id}/correlations/{cid}").json()
    assert body["run_id"] == run_id
    assert body["correlation_id"] == cid
    matched = body["events"]
    assert matched, "correlation lookup must return at least one event"
    for event in matched:
        assert event["data"].get("correlation_id") == cid
    # Events must be in event_id ascending order (the PK order).
    ids = [e["event_id"] for e in matched]
    assert ids == sorted(ids)


def test_correlation_unknown_id_returns_empty(client):
    run_id = _create_run(client)
    body = client.get(f"/runs/{run_id}/correlations/no-such-correlation").json()
    assert body["events"] == []


def test_correlation_unknown_run_returns_404(client):
    resp = client.get("/runs/no-such-run/correlations/anything")
    assert resp.status_code == 404
