"""Population builder endpoint tests."""

from __future__ import annotations


class TestPopulationBuild:
    def test_build_mixed_population(self, client):
        resp = client.post("/population/build", json={
            "mix": {"noise": 0.6, "informed": 0.4},
            "total_agents": 10,
            "default_collateral": 1_000_000,
            "seed": 42,
        })
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        assert len(agents) == 10
        types = [a["type"] for a in agents]
        assert types.count("NoiseTrader") == 6
        assert types.count("InformedTrader") == 4

    def test_build_single_role(self, client):
        resp = client.post("/population/build", json={
            "mix": {"noise": 1.0},
            "total_agents": 5,
        })
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 5

    def test_build_with_role_params(self, client):
        resp = client.post("/population/build", json={
            "mix": {"noise": 1.0},
            "total_agents": 3,
            "role_params": {"noise": {"collateral": "USDC"}},
        })
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 3

    def test_build_all_role_types(self, client):
        resp = client.post("/population/build", json={
            "mix": {
                "noise": 0.2,
                "informed": 0.2,
                "arbitrageur": 0.2,
                "lp": 0.2,
                "rebalancing_lp": 0.2,
            },
            "total_agents": 10,
        })
        assert resp.status_code == 200
        assert len(resp.json()["agents"]) == 10

    def test_invalid_mix_returns_422(self, client):
        resp = client.post("/population/build", json={
            "mix": {"noise": 0.5},  # doesn't sum to 1.0
            "total_agents": 10,
        })
        assert resp.status_code == 422

    def test_agents_have_collateral(self, client):
        resp = client.post("/population/build", json={
            "mix": {"noise": 1.0},
            "total_agents": 2,
            "default_collateral": 5_000_000,
        })
        agents = resp.json()["agents"]
        for a in agents:
            assert a["balances"]["COLLATERAL"] == 5_000_000


class TestPopulationRoles:
    def test_list_roles(self, client):
        resp = client.get("/population/roles")
        assert resp.status_code == 200
        roles = resp.json()
        assert "noise" in roles
        assert "informed" in roles
        assert "arbitrageur" in roles
        assert "lp" in roles
        assert "rebalancing_lp" in roles
