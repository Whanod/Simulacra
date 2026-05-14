"""Reports CRUD endpoint tests (G3 in plan-api.md)."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


class TestListReports:
    def test_list_empty(self, client):
        resp = client.get("/reports")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reports"] == []
        assert body["count"] == 0
        assert body["limit"] == 100
        assert body["offset"] == 0

    def test_list_contains_created_report(self, client):
        created = client.post(
            "/reports",
            json={"title": "Alpha", "run_ids": [], "sweep_ids": []},
        ).json()
        report_id = created["report_id"]

        body = client.get("/reports").json()
        assert body["count"] == 1
        assert [r["report_id"] for r in body["reports"]] == [report_id]
        entry = body["reports"][0]
        assert entry["status"] == "draft"
        assert "manifest" in entry and entry["manifest"]["title"] == "Alpha"

    def test_list_newest_first(self, client):
        ids = []
        for title in ("one", "two", "three"):
            ids.append(
                client.post("/reports", json={"title": title}).json()["report_id"]
            )
        body = client.get("/reports").json()
        assert [r["report_id"] for r in body["reports"]] == list(reversed(ids))
        assert body["count"] == 3

    def test_list_pagination(self, client):
        ids = [
            client.post("/reports", json={"title": f"r{i}"}).json()["report_id"]
            for i in range(5)
        ]
        expected = list(reversed(ids))

        page1 = client.get("/reports", params={"limit": 2, "offset": 0}).json()
        assert [r["report_id"] for r in page1["reports"]] == expected[0:2]
        assert page1["count"] == 5
        assert page1["limit"] == 2
        assert page1["offset"] == 0

        page2 = client.get("/reports", params={"limit": 2, "offset": 2}).json()
        assert [r["report_id"] for r in page2["reports"]] == expected[2:4]

        page3 = client.get("/reports", params={"limit": 2, "offset": 4}).json()
        assert [r["report_id"] for r in page3["reports"]] == expected[4:5]


class TestUpdateReport:
    def test_update_title(self, client):
        created = client.post("/reports", json={"title": "Original"}).json()
        rid = created["report_id"]

        resp = client.put(f"/reports/{rid}", json={"title": "Renamed"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["manifest"]["title"] == "Renamed"

        reread = client.get(f"/reports/{rid}").json()
        assert reread["manifest"]["title"] == "Renamed"

    def test_update_status(self, client):
        rid = client.post("/reports", json={"title": "S"}).json()["report_id"]

        resp = client.put(f"/reports/{rid}", json={"status": "published"})
        assert resp.status_code == 200
        assert resp.json()["report"]["status"] == "published"
        assert client.get(f"/reports/{rid}").json()["report"]["status"] == "published"

    def test_update_sections(self, client):
        rid = client.post("/reports", json={"title": "Secs"}).json()["report_id"]

        new_sections = [
            {"id": "sec-1", "type": "summary", "title": "Overview"},
            {"id": "sec-2", "type": "chart", "title": "Price", "runId": "run-x"},
        ]
        resp = client.put(f"/reports/{rid}", json={"sections": new_sections})
        assert resp.status_code == 200
        assert resp.json()["manifest"]["sections"] == new_sections

        reread = client.get(f"/reports/{rid}").json()
        assert reread["manifest"]["sections"] == new_sections

    def test_update_run_ids_and_charts(self, client):
        rid = client.post("/reports", json={"title": "Lists"}).json()["report_id"]
        patch = {
            "run_ids": ["run-a", "run-b"],
            "charts": [{"type": "leaderboard"}, {"type": "time_series"}],
        }
        resp = client.put(f"/reports/{rid}", json=patch)
        assert resp.status_code == 200
        manifest = resp.json()["manifest"]
        assert manifest["run_ids"] == patch["run_ids"]
        assert manifest["charts"] == patch["charts"]

    def test_update_preserves_other_fields(self, client):
        rid = client.post(
            "/reports",
            json={"title": "Keep", "run_ids": ["existing"], "charts": [{"type": "box"}]},
        ).json()["report_id"]

        resp = client.put(f"/reports/{rid}", json={"title": "Updated"})
        manifest = resp.json()["manifest"]
        assert manifest["title"] == "Updated"
        assert manifest["run_ids"] == ["existing"]
        assert manifest["charts"] == [{"type": "box"}]

    def test_update_unknown_status_returns_422(self, client):
        rid = client.post("/reports", json={"title": "X"}).json()["report_id"]
        resp = client.put(f"/reports/{rid}", json={"status": "weird"})
        assert resp.status_code == 422

    def test_update_unknown_id_returns_404(self, client):
        resp = client.put("/reports/does-not-exist", json={"title": "nope"})
        assert resp.status_code == 404


class TestDeleteReport:
    def test_delete_removes_from_list(self, client):
        rid = client.post("/reports", json={"title": "D"}).json()["report_id"]
        assert client.get("/reports").json()["count"] == 1

        resp = client.delete(f"/reports/{rid}")
        assert resp.status_code == 204

        body = client.get("/reports").json()
        assert body["count"] == 0
        assert body["reports"] == []

    def test_get_after_delete_returns_404(self, client):
        rid = client.post("/reports", json={"title": "D2"}).json()["report_id"]
        client.delete(f"/reports/{rid}")
        assert client.get(f"/reports/{rid}").status_code == 404

    def test_delete_unknown_id_returns_404(self, client):
        assert client.delete("/reports/does-not-exist").status_code == 404

    def test_bundle_is_regenerated_on_every_request(self, client):
        """Phase 3 dropped bundle caching — every /bundle hit rebuilds from
        current SQL state. Two consecutive requests must both succeed and
        produce identical ZIP bytes (the underlying data hasn't changed)."""
        run_id = client.post("/simulations/run", json=CFAMM_SPEC).json()["run_id"]
        rid = client.post(
            "/reports",
            json={"title": "Bundled", "run_ids": [run_id]},
        ).json()["report_id"]

        first = client.get(f"/reports/{rid}/bundle")
        assert first.status_code == 200
        assert first.headers["content-type"] == "application/zip"

        second = client.get(f"/reports/{rid}/bundle")
        assert second.status_code == 200
        # Same input → same ZIP. Catches a regression where regeneration
        # picks up non-deterministic content (e.g. timestamps in manifest).
        assert first.content == second.content

        resp = client.delete(f"/reports/{rid}")
        assert resp.status_code == 204
        assert client.get(f"/reports/{rid}").status_code == 404
        # Bundle download after delete must 404, not silently rebuild from
        # nothing.
        assert client.get(f"/reports/{rid}/bundle").status_code == 404
