"""BE-006 contract test — guarantees the registry contract cannot
silently regress. Every registered builtin must carry label, schema,
ui_schema, and defaults, and the /registry endpoint must emit the
versioned top-level shape even when filtered to an unknown category.
"""

from __future__ import annotations

import pytest

from defi_sim.engine.specs import (
    _CATEGORY_TABLES,
    AgentSpec,
    ClockSpec,
    ExecutionSpec,
    FeeModelSpec,
    FeedSpec,
    GasSpec,
    InformationFilterSpec,
    MarketSpec,
    OrderingSpec,
    TokenSpec,
    build_agent,
    build_clock,
    build_execution_model,
    build_fee_model,
    build_feed,
    build_gas_model,
    build_information_filter,
    build_market,
    build_ordering,
    get_registry_metadata,
    iter_registry_categories,
)
from defi_sim_api.routers.registry import REGISTRY_CONTRACT_VERSION


class TestContractInvariants:
    """Every registered builtin must carry the four fields the
    frontend renderer depends on: label, schema, ui_schema, defaults.
    """

    @pytest.mark.parametrize(
        "category,spec_type",
        [
            (cat, spec_type)
            for cat in iter_registry_categories()
            for spec_type in _CATEGORY_TABLES[cat][0]
        ],
    )
    def test_every_builtin_has_contract_fields(self, category, spec_type):
        meta = get_registry_metadata(category, spec_type)
        assert meta is not None, f"{category}/{spec_type} missing metadata"
        assert meta.label, f"{category}/{spec_type} missing label"
        assert meta.schema is not None, f"{category}/{spec_type} missing schema"
        assert meta.ui_schema is not None, f"{category}/{spec_type} missing ui_schema"
        assert meta.defaults is not None, f"{category}/{spec_type} missing defaults"


class TestBuildRoundTrips:
    """Shipped defaults must actually build without raising.
    Consolidates the per-category round-trips into one place so BE-006
    has a single source of truth."""

    def test_every_builtin_default_builds(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                params = dict(meta.defaults or {})
                if category == "markets":
                    if spec_type == "world":
                        # world goes through a separate dispatch path
                        continue
                    if spec_type == "clob":
                        build_market({
                            "type": "clob",
                            "pairs": [
                                {
                                    "base": {"id": "A", "symbol": "A", "decimals": 18},
                                    "quote": {"id": "B", "symbol": "B", "decimals": 18},
                                }
                            ],
                        })
                        continue
                    build_market(
                        MarketSpec(
                            type=spec_type,
                            tokens=[
                                TokenSpec(id="A", symbol="A", decimals=18),
                                TokenSpec(id="B", symbol="B", decimals=18),
                            ],
                            params=params,
                        )
                    )
                elif category == "agents":
                    build_agent(
                        AgentSpec(
                            type=spec_type,
                            agent_id=f"{spec_type}-0",
                            params=params,
                        )
                    )
                elif category == "clocks":
                    build_clock(ClockSpec(type=spec_type, params=params))
                elif category == "orderings":
                    build_ordering(OrderingSpec(type=spec_type, params=params))
                elif category == "gas_models":
                    build_gas_model(GasSpec(type=spec_type, params=params))
                elif category == "fee_models":
                    build_fee_model(FeeModelSpec(type=spec_type, params=params))
                elif category == "feeds":
                    if spec_type == "composite":
                        continue
                    build_feed(FeedSpec(type=spec_type, params=params))
                elif category == "execution_models":
                    build_execution_model(ExecutionSpec(type=spec_type, params=params))
                elif category == "information_filters":
                    build_information_filter(
                        InformationFilterSpec(type=spec_type, params=params)
                    )


class TestVersionedTopLevelShape:
    def test_every_response_carries_contract_version(self, client):
        body = client.get("/registry").json()
        assert body["contractVersion"] == REGISTRY_CONTRACT_VERSION
        assert "categories" in body

    def test_unknown_category_still_returns_404(self, client):
        resp = client.get("/registry/unicorns")
        assert resp.status_code == 404

    def test_registry_response_parseable_without_coercion(self, client):
        """Smoke test: the live /registry response must be parseable
        into the enriched shape with all the fields the frontend
        renderer depends on, without any coercion step.
        """
        body = client.get("/registry").json()
        assert body["contractVersion"] == REGISTRY_CONTRACT_VERSION
        for cat in body["categories"]:
            assert cat["key"].startswith("reg-")
            assert cat["label"]
            for entity in cat["entities"]:
                assert entity["label"]
                assert "schema" in entity
                assert "uiSchema" in entity
                assert "defaults" in entity
                assert "builderSupported" in entity
