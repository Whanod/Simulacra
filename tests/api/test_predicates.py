"""Predicate builder and evaluation endpoint tests."""

from __future__ import annotations


class TestPredicateBuild:
    def test_build_threshold(self, client):
        resp = client.post("/predicates/build", json={
            "type": "threshold",
            "params": {"field": "price", "op": ">", "threshold": 100},
        })
        assert resp.status_code == 200
        pred = resp.json()["predicate"]
        assert pred["field"] == "price"

    def test_build_and_predicate(self, client):
        resp = client.post("/predicates/build", json={
            "type": "and",
            "children": [
                {"type": "threshold", "params": {"field": "price", "op": ">", "threshold": 50}},
                {"type": "threshold", "params": {"field": "volume", "op": "<", "threshold": 1000}},
            ],
        })
        assert resp.status_code == 200
        pred = resp.json()["predicate"]
        assert pred["type"] == "and"

    def test_build_or_predicate(self, client):
        resp = client.post("/predicates/build", json={
            "type": "or",
            "children": [
                {"type": "threshold", "params": {"field": "price", "op": "<", "threshold": 10}},
                {"type": "threshold", "params": {"field": "price", "op": ">", "threshold": 100}},
            ],
        })
        assert resp.status_code == 200

    def test_build_not_predicate(self, client):
        resp = client.post("/predicates/build", json={
            "type": "not",
            "child": {"type": "threshold", "params": {"field": "price", "op": ">", "threshold": 100}},
        })
        assert resp.status_code == 200

    def test_build_unknown_type_422(self, client):
        resp = client.post("/predicates/build", json={"type": "unicorn"})
        assert resp.status_code == 422


class TestPredicateEvaluate:
    def test_threshold_true(self, client):
        resp = client.post("/predicates/evaluate", json={
            "predicate": {
                "type": "threshold",
                "params": {"field": "price", "source": "market", "op": ">", "threshold": 50},
            },
            "market_state": {"price": 100},
            "agent_state": {},
        })
        assert resp.status_code == 200
        assert resp.json()["result"] is True

    def test_threshold_false(self, client):
        resp = client.post("/predicates/evaluate", json={
            "predicate": {
                "type": "threshold",
                "params": {"field": "price", "source": "market", "op": ">", "threshold": 200},
            },
            "market_state": {"price": 100},
            "agent_state": {},
        })
        assert resp.status_code == 200
        assert resp.json()["result"] is False

    def test_and_both_true(self, client):
        resp = client.post("/predicates/evaluate", json={
            "predicate": {
                "type": "and",
                "children": [
                    {"type": "threshold", "params": {"field": "price", "op": ">", "threshold": 50}},
                    {"type": "threshold", "params": {"field": "volume", "op": "<", "threshold": 1000}},
                ],
            },
            "market_state": {"price": 100, "volume": 500},
            "agent_state": {},
        })
        assert resp.status_code == 200
        assert resp.json()["result"] is True

    def test_not_negates(self, client):
        resp = client.post("/predicates/evaluate", json={
            "predicate": {
                "type": "not",
                "child": {"type": "threshold", "params": {"field": "price", "op": ">", "threshold": 200}},
            },
            "market_state": {"price": 100},
            "agent_state": {},
        })
        assert resp.status_code == 200
        assert resp.json()["result"] is True

    def test_agent_source(self, client):
        resp = client.post("/predicates/evaluate", json={
            "predicate": {
                "type": "threshold",
                "params": {"field": "balance", "source": "agent", "op": ">=", "threshold": 1000},
            },
            "market_state": {},
            "agent_state": {"balance": 5000},
        })
        assert resp.status_code == 200
        assert resp.json()["result"] is True
