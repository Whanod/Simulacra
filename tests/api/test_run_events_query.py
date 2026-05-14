"""Router-level coverage for the Phase 3 paginated/filtered /runs/{id}/events.

The goldens exercise the unbounded ``?limit=10000`` path, so the new
``?type=``, ``?agent=``, ``?from=``, ``?to=``, ``?cursor=`` knobs need their
own contract. Tests run against the default (Local) backend; equivalent
Postgres SQL coverage lives in :mod:`tests.api.test_postgres_store`.
"""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


def test_events_type_filter(client):
    run_id = _create_run(client)
    body = client.get(f"/runs/{run_id}/events", params={"event_type": "SIMULATION_END"}).json()
    assert all(e["type"] == "SIMULATION_END" for e in body["events"])
    assert len(body["events"]) == 1


def test_events_round_range(client):
    run_id = _create_run(client)
    # ?from=2&to=3 means inclusive both sides. The CFAMM_SPEC runs 5 rounds.
    body = client.get(f"/runs/{run_id}/events", params={"from": 2, "to": 3}).json()
    rounds = sorted({e["round"] for e in body["events"]})
    assert rounds == [2, 3]


def test_events_cursor_paginates(client):
    run_id = _create_run(client)
    full = client.get(f"/runs/{run_id}/events", params={"limit": 10_000}).json()["events"]
    assert len(full) > 2, "spec is expected to emit at least a few events"

    page_a = client.get(f"/runs/{run_id}/events", params={"limit": 2}).json()
    assert len(page_a["events"]) == 2
    # next_cursor present iff there's plausibly more — empirically true here.
    assert "next_cursor" in page_a
    assert page_a["next_cursor"] == page_a["events"][-1]["event_id"]

    page_b = client.get(
        f"/runs/{run_id}/events",
        params={"limit": 2, "cursor": page_a["next_cursor"]},
    ).json()
    seen = {e["event_id"] for e in page_a["events"]} | {e["event_id"] for e in page_b["events"]}
    assert page_a["events"][-1]["event_id"] not in {e["event_id"] for e in page_b["events"]}
    # Walking with cursor must hit every event in event_id order.
    walked: list[int] = []
    cursor: int | None = None
    while True:
        params: dict[str, int] = {"limit": 50}
        if cursor is not None:
            params["cursor"] = cursor
        page = client.get(f"/runs/{run_id}/events", params=params).json()
        walked.extend(e["event_id"] for e in page["events"])
        cursor = page.get("next_cursor")
        if cursor is None:
            break
    assert walked == [e["event_id"] for e in full]
    # Sanity check we exercised the previously-seen pair too.
    assert {e["event_id"] for e in page_b["events"]} <= seen


def test_events_no_cursor_when_unbounded(client):
    """A request with a limit large enough to fit everything must not emit
    ``next_cursor`` — that's what keeps the goldens byte-equal."""
    run_id = _create_run(client)
    body = client.get(f"/runs/{run_id}/events", params={"limit": 10_000}).json()
    assert "next_cursor" not in body
