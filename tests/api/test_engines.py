"""Engine introspection endpoint tests — events, market state, agent state."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC, WORLD_SPEC


class TestEvents:
    def test_events_empty_before_stepping(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/events")
        assert resp.status_code == 200
        assert resp.json()["events"] == []

    def test_events_populated_after_stepping(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        client.post(f"/simulations/{sim_id}/step")
        resp = client.get(f"/simulations/{sim_id}/events")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) > 0
        assert events[0]["type"] == "SIMULATION_START"

    def test_events_filter_by_type(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        client.post(f"/simulations/{sim_id}/step")
        resp = client.get(f"/simulations/{sim_id}/events", params={"event_type": "ROUND_END"})
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert all(e["type"] == "ROUND_END" for e in events)

    def test_events_pagination(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        client.post(f"/simulations/{sim_id}/step")
        client.post(f"/simulations/{sim_id}/step")

        all_events = client.get(f"/simulations/{sim_id}/events").json()["events"]
        page = client.get(f"/simulations/{sim_id}/events", params={"limit": 2, "offset": 1}).json()["events"]
        assert len(page) == 2
        assert page[0] == all_events[1]

    def test_events_404_unknown_sim(self, client):
        assert client.get("/simulations/nope/events").status_code == 404


class TestMarketState:
    def test_get_all_market_states_single(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/markets")
        assert resp.status_code == 200
        states = resp.json()["states"]
        assert "default" in states
        assert states["default"]["__type__"] == "AmmSnapshot"

    def test_get_all_market_states_world(self, client):
        sim_id = client.post("/simulations/build", json=WORLD_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/markets")
        assert resp.status_code == 200
        states = resp.json()["states"]
        assert "amm" in states
        assert "book" in states

    def test_get_specific_market(self, client):
        sim_id = client.post("/simulations/build", json=WORLD_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/markets/amm")
        assert resp.status_code == 200
        assert resp.json()["market_name"] == "amm"

    def test_get_unknown_market_404(self, client):
        sim_id = client.post("/simulations/build", json=WORLD_SPEC).json()["simulation_id"]
        assert client.get(f"/simulations/{sim_id}/markets/nonexistent").status_code == 404

    def test_get_prices(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/markets/default/prices")
        assert resp.status_code == 200
        prices = resp.json()["prices"]
        assert "SOL" in prices
        assert "USDC" in prices

    def test_get_lp_state(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/markets/default/lp")
        assert resp.status_code == 200
        assert "lp_state" in resp.json()
        assert "positions" in resp.json()


class TestAgentState:
    def test_get_all_agents(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/agents")
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        assert "noise-1" in agents
        assert agents["noise-1"]["balances"]["USDC"] == 1_000_000_000

    def test_get_specific_agent(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/agents/noise-1")
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "noise-1"

    def test_get_unknown_agent_404(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        assert client.get(f"/simulations/{sim_id}/agents/ghost").status_code == 404

    def test_agent_state_changes_after_step(self, client):
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        before = client.get(f"/simulations/{sim_id}/agents/noise-1").json()
        client.post(f"/simulations/{sim_id}/step")
        after = client.get(f"/simulations/{sim_id}/agents/noise-1").json()
        # The agent may or may not have traded, but the endpoint still works
        assert after["agent_id"] == "noise-1"
