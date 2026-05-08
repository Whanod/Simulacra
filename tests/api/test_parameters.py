"""Parameter store endpoint tests."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


class TestParameterStore:
    def _build(self, client):
        spec = {**CFAMM_SPEC, "parameters": {"fee_bps": 30, "max_leverage": 10}}
        return client.post("/simulations/build", json=spec).json()["simulation_id"]

    def test_get_parameters(self, client):
        sim_id = self._build(client)
        resp = client.get(f"/simulations/{sim_id}/parameters")
        assert resp.status_code == 200
        assert resp.json()["params"]["fee_bps"] == 30

    def test_get_single_parameter(self, client):
        sim_id = self._build(client)
        resp = client.get(f"/simulations/{sim_id}/parameters/fee_bps")
        assert resp.status_code == 200
        assert resp.json()["value"] == 30

    def test_get_missing_parameter_404(self, client):
        sim_id = self._build(client)
        assert client.get(f"/simulations/{sim_id}/parameters/nonexistent").status_code == 404

    def test_set_parameter(self, client):
        sim_id = self._build(client)
        resp = client.put(
            f"/simulations/{sim_id}/parameters",
            json={"key": "fee_bps", "value": 50},
        )
        assert resp.status_code == 200
        assert resp.json()["old_value"] == 30
        assert resp.json()["new_value"] == 50

        # Verify it stuck
        assert client.get(f"/simulations/{sim_id}/parameters/fee_bps").json()["value"] == 50

    def test_schedule_parameter(self, client):
        sim_id = self._build(client)
        resp = client.post(
            f"/simulations/{sim_id}/parameters/schedule",
            json={"key": "fee_bps", "value": 100, "execute_at_round": 3},
        )
        assert resp.status_code == 201
        assert resp.json()["scheduled"] is True

        # Check it shows up in pending
        store = client.get(f"/simulations/{sim_id}/parameters").json()
        assert len(store["pending"]) == 1
        assert store["pending"][0]["key"] == "fee_bps"

    def test_parameter_history(self, client):
        sim_id = self._build(client)
        client.put(f"/simulations/{sim_id}/parameters", json={"key": "fee_bps", "value": 50})
        client.put(f"/simulations/{sim_id}/parameters", json={"key": "fee_bps", "value": 75})

        resp = client.get(f"/simulations/{sim_id}/parameters/history", params={"key": "fee_bps"})
        assert resp.status_code == 200
        history = resp.json()["history"]
        assert len(history) == 2
        assert history[0]["old_value"] == 30
        assert history[1]["new_value"] == 75

    def test_parameter_history_all(self, client):
        sim_id = self._build(client)
        client.put(f"/simulations/{sim_id}/parameters", json={"key": "fee_bps", "value": 50})
        resp = client.get(f"/simulations/{sim_id}/parameters/history")
        assert resp.status_code == 200
        assert len(resp.json()["history"]) >= 1
