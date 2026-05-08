"""Order book introspection endpoint tests."""

from __future__ import annotations

from tests.api.conftest import WORLD_SPEC


class TestOrderbook:
    def test_get_orderbook_from_world(self, client):
        sim_id = client.post("/simulations/build", json=WORLD_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/orderbook", params={"market_name": "book"})
        assert resp.status_code == 200
        books = resp.json()["books"]
        assert len(books) > 0
        # SOL:USDC pair
        key = list(books.keys())[0]
        assert "bids" in books[key]
        assert "asks" in books[key]

    def test_get_orderbook_auto_finds_clob(self, client):
        sim_id = client.post("/simulations/build", json=WORLD_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/orderbook")
        assert resp.status_code == 200

    def test_orderbook_no_clob_returns_400(self, client):
        from tests.api.conftest import CFAMM_SPEC
        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}/orderbook")
        assert resp.status_code == 400

    def test_orderbook_404_unknown_sim(self, client):
        assert client.get("/simulations/nope/orderbook").status_code == 404
