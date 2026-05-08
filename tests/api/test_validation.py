"""Validation check endpoint tests."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC, WORLD_SPEC


class TestValidationCheck:
    def test_valid_spec_passes_all_checks(self, client):
        resp = client.post("/validation/check", json={
            "spec": CFAMM_SPEC,
            "checks": ["solvency", "reserves"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is True
        assert body["details"]["solvency"]["ok"] is True
        assert body["details"]["reserves"]["ok"] is True

    def test_world_spec_passes(self, client):
        resp = client.post("/validation/check", json={
            "spec": WORLD_SPEC,
            "checks": ["solvency", "reserves"],
        })
        assert resp.status_code == 200
        assert resp.json()["passed"] is True

    def test_invalid_spec_returns_build_error(self, client):
        resp = client.post("/validation/check", json={
            "spec": {"market": {"type": "nonexistent"}, "agents": []},
            "checks": ["solvency"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is False
        assert "build_error" in body["details"]

    def test_solvency_only(self, client):
        resp = client.post("/validation/check", json={
            "spec": CFAMM_SPEC,
            "checks": ["solvency"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "solvency" in body["details"]
        assert "reserves" not in body["details"]

    def test_conservation_check(self, client):
        resp = client.post("/validation/check", json={
            "spec": CFAMM_SPEC,
            "checks": ["conservation"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is True
        assert body["details"]["conservation"]["ok"] is True

    def test_all_checks_together(self, client):
        resp = client.post("/validation/check", json={
            "spec": CFAMM_SPEC,
            "checks": ["solvency", "reserves", "conservation"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is True
        assert len(body["details"]) == 3


class TestValidationHook:
    def test_attach_hook(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.post(f"/validation/hook/{sim_id}", params={"checks": ["solvency", "reserves"]})
        assert resp.status_code == 201
        assert resp.json()["attached"] is True

    def test_get_violations_empty(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        client.post(f"/validation/hook/{sim_id}")

        # Step a few times — no violations expected for a simple setup
        for _ in range(3):
            client.post(f"/simulations/{sim_id}/step")

        resp = client.get(f"/validation/hook/{sim_id}/violations")
        assert resp.status_code == 200
        assert resp.json()["violations"] == []

    def test_violations_404_no_hook(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        assert client.get(f"/validation/hook/{sim_id}/violations").status_code == 404

    def test_hook_404_unknown_sim(self, client):
        assert client.post("/validation/hook/nope").status_code == 404
