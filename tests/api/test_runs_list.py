"""GET /runs list/pagination tests (G6 in plan-api.md).

Confirms that the `count` field reflects the true total, not the page size.
"""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


def _create_run(client) -> str:
    resp = client.post("/simulations/run", json=CFAMM_SPEC)
    assert resp.status_code == 200
    return resp.json()["run_id"]


class TestListRunsPagination:
    def test_empty_list(self, client):
        body = client.get("/runs").json()
        assert body["runs"] == []
        assert body["count"] == 0
        assert body["limit"] == 100
        assert body["offset"] == 0

    def test_count_is_true_total_not_page_size(self, client):
        run_ids = [_create_run(client) for _ in range(3)]

        body = client.get("/runs", params={"limit": 1}).json()
        assert len(body["runs"]) == 1
        assert body["count"] == 3, "count must reflect the total, not the page size"
        assert body["limit"] == 1
        assert body["offset"] == 0

        # Full pagination round-trip
        page1 = client.get("/runs", params={"limit": 1, "offset": 0}).json()
        page2 = client.get("/runs", params={"limit": 1, "offset": 1}).json()
        page3 = client.get("/runs", params={"limit": 1, "offset": 2}).json()
        all_ids = [
            page1["runs"][0]["run_id"],
            page2["runs"][0]["run_id"],
            page3["runs"][0]["run_id"],
        ]
        assert set(all_ids) == set(run_ids)
        assert page3["count"] == 3
        assert page3["offset"] == 2

    def test_newest_first_ordering(self, client):
        first = _create_run(client)
        second = _create_run(client)
        body = client.get("/runs").json()
        ordered = [item["run_id"] for item in body["runs"]]
        assert ordered[0] == second
        assert ordered[1] == first
