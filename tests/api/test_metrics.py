"""Metrics endpoint tests."""

from __future__ import annotations

import math


class TestListMetrics:
    def test_returns_batch_and_streaming(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        assert "batch" in body
        assert "streaming" in body
        assert "kl_divergence" in body["batch"]
        assert "manipulation_resistance_revised" in body["batch"]
        assert "max_drawdown" in body["streaming"]
        assert "rolling_volatility" in body["streaming"]
        assert "twap" in body["streaming"]


class TestComputeMetrics:
    def test_kl_divergence(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "kl": {
                    "type": "kl_divergence",
                    "params": {
                        "p": [0.5, 0.5],
                        "q": [0.5, 0.5],
                    },
                },
            },
        })
        assert resp.status_code == 200
        assert resp.json()["metrics"]["kl"] == 0.0

    def test_kl_divergence_nonzero(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "kl": {
                    "type": "kl_divergence",
                    "params": {
                        "p": [0.9, 0.1],
                        "q": [0.5, 0.5],
                    },
                },
            },
        })
        assert resp.status_code == 200
        assert resp.json()["metrics"]["kl"] > 0

    def test_convergence_speed(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "speed": {
                    "type": "convergence_speed",
                    "params": {
                        "series": [1.0, 0.5, 0.005, 0.003, 0.001],
                        "threshold": 0.01,
                    },
                },
            },
        })
        assert resp.status_code == 200
        assert resp.json()["metrics"]["speed"] == 2.0

    def test_lp_profitability(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "lp_profit": {
                    "type": "lp_profitability",
                    "params": {
                        "fees_earned": 100,
                        "capital_deposited": 1000,
                        "impermanent_loss": 20,
                    },
                },
            },
        })
        assert resp.status_code == 200
        assert abs(resp.json()["metrics"]["lp_profit"] - 0.08) < 1e-9

    def test_unknown_metric_skipped(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "mystery": {"type": "does_not_exist", "params": {}},
            },
        })
        assert resp.status_code == 200
        assert resp.json()["metrics"] == {}

    def test_empty_metrics_returns_empty(self, client):
        resp = client.post("/metrics/compute", json={"result": {}, "metrics": {}})
        assert resp.status_code == 200
        assert resp.json()["metrics"] == {}

    def test_manipulation_resistance_revised(self, client):
        resp = client.post("/metrics/compute", json={
            "result": {},
            "metrics": {
                "mr": {
                    "type": "manipulation_resistance_revised",
                    "params": {"budget": 1000, "price_change": 10, "payout_improvement": 5},
                },
            },
        })
        assert resp.status_code == 200
        assert abs(resp.json()["metrics"]["mr"] - (1000 / 15)) < 1e-6


class TestStreamingMetrics:
    def test_register_and_finalize(self, client):
        from tests.api.conftest import CFAMM_SPEC

        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]

        # Register streaming metrics
        resp = client.post(f"/metrics/streaming/{sim_id}/register", json={
            "drawdown": {"type": "max_drawdown", "params": {}},
        })
        assert resp.status_code == 201
        assert "drawdown" in resp.json()["registered"]

        # Step the engine a few times
        for _ in range(3):
            client.post(f"/simulations/{sim_id}/step")

        # Finalize
        resp = client.get(f"/metrics/streaming/{sim_id}")
        assert resp.status_code == 200
        assert "drawdown" in resp.json()["metrics"]

    def test_finalize_without_register_returns_empty(self, client):
        from tests.api.conftest import CFAMM_SPEC

        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.get(f"/metrics/streaming/{sim_id}")
        assert resp.status_code == 200
        assert resp.json()["metrics"] == {}

    def test_register_unknown_metric_skipped(self, client):
        from tests.api.conftest import CFAMM_SPEC

        sim_id = client.post("/simulations/build", json=CFAMM_SPEC).json()["simulation_id"]
        resp = client.post(f"/metrics/streaming/{sim_id}/register", json={
            "mystery": {"type": "nonexistent"},
        })
        assert resp.status_code == 201
        assert resp.json()["registered"] == []

    def test_register_404_unknown_sim(self, client):
        resp = client.post("/metrics/streaming/nope/register", json={})
        assert resp.status_code == 404
