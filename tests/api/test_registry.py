"""Registry / catalog endpoint tests."""

from __future__ import annotations

from tests.api.conftest import CFAMM_SPEC


class TestRegistryListAllEnriched:
    def test_returns_contract_response_by_default(self, client):
        resp = client.get("/registry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["contractVersion"] == "v2"
        assert isinstance(body["categories"], list)

    def test_enriched_response_covers_all_categories(self, client):
        body = client.get("/registry").json()
        keys = {cat["key"] for cat in body["categories"]}
        for expected in (
            "reg-markets",
            "reg-agents",
            "reg-clocks",
            "reg-ordering",
            "reg-gas",
            "reg-fees",
            "reg-feeds",
            "reg-exec",
            "reg-information",
        ):
            assert expected in keys

    def test_every_entity_has_label_and_builder_supported(self, client):
        body = client.get("/registry").json()
        for cat in body["categories"]:
            for entity in cat["entities"]:
                assert entity["label"], f"entity missing label: {entity}"
                assert "builderSupported" in entity

    def test_markets_includes_cfamm_and_clob(self, client):
        body = client.get("/registry").json()
        markets_cat = next(c for c in body["categories"] if c["key"] == "reg-markets")
        types = {e["type"] for e in markets_cat["entities"]}
        assert "cfamm" in types
        assert "clob" in types

    def test_agents_includes_builtin_types(self, client):
        body = client.get("/registry").json()
        agents_cat = next(c for c in body["categories"] if c["key"] == "reg-agents")
        types = {e["type"] for e in agents_cat["entities"]}
        for expected in ("noise", "informed", "arbitrageur", "lp", "manipulator"):
            assert expected in types

    def test_unregistered_entity_falls_back_to_title_cased_label(self, client):
        """Entities registered without EntityMetadata (the default today,
        before BE-003) must still return a non-empty label derived from
        their spec type."""
        body = client.get("/registry").json()
        for cat in body["categories"]:
            for entity in cat["entities"]:
                assert entity["label"] != ""


class TestRegistryCategory:
    def test_enriched_category_response(self, client):
        resp = client.get("/registry/orderings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "reg-ordering"
        assert body["label"] == "Ordering"
        assert isinstance(body["entities"], list)
        types = {e["type"] for e in body["entities"]}
        assert "fifo" in types
        assert "priority" in types
        assert "random" in types

    def test_unknown_category_returns_404(self, client):
        resp = client.get("/registry/unicorns")
        assert resp.status_code == 404


class TestSpecValidation:
    def test_valid_spec_passes(self, client):
        resp = client.post("/registry/validate", json=CFAMM_SPEC)
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["errors"] == []

    def test_invalid_spec_returns_errors(self, client):
        bad_spec = {
            **CFAMM_SPEC,
            "market": {"type": "nonexistent_market"},
        }
        resp = client.post("/registry/validate", json=bad_spec)
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0

    def test_missing_market_returns_422(self, client):
        resp = client.post("/registry/validate", json={"agents": []})
        assert resp.status_code == 422
