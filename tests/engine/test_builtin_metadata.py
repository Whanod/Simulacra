"""Content tests for builtin EntityMetadata (BE-003 / BE-004 / BE-005)."""

from __future__ import annotations

from defi_sim.engine.specs import (
    _CATEGORY_TABLES,
    get_registry_metadata,
    iter_registry_categories,
)


class TestBuiltinLabelsAndDescriptions:
    def test_every_builtin_has_non_empty_label(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None, f"{category}/{spec_type} missing metadata"
                assert meta.label, f"{category}/{spec_type} has empty label"

    def test_every_builtin_has_non_empty_description(self):
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                assert meta.description, (
                    f"{category}/{spec_type} has empty description"
                )

    def test_builder_supported_builtins_do_not_use_placeholder_labels(self):
        """Spec type BE-003: no entity marked builder_supported=True may
        use a reserved placeholder label like "TODO"."""
        for category in iter_registry_categories():
            factories, _ = _CATEGORY_TABLES[category]
            for spec_type in factories:
                meta = get_registry_metadata(category, spec_type)
                assert meta is not None
                if meta.builder_supported:
                    assert meta.label.upper() != "TODO"
                    assert "TODO" not in meta.label


class TestBuilderSupportFlags:
    def test_historical_feed_is_not_builder_supported(self):
        meta = get_registry_metadata("feeds", "historical")
        assert meta is not None
        assert meta.builder_supported is False

    def test_composite_feed_is_not_builder_supported(self):
        meta = get_registry_metadata("feeds", "composite")
        assert meta is not None
        assert meta.builder_supported is False

    def test_stochastic_feed_is_builder_supported(self):
        meta = get_registry_metadata("feeds", "stochastic")
        assert meta is not None
        assert meta.builder_supported is True


class TestBuiltinBadges:
    def test_cfamm_has_market_badges(self):
        meta = get_registry_metadata("markets", "cfamm")
        assert meta is not None
        assert meta.badges is not None
        labels = {badge["label"] for badge in meta.badges}
        assert "PricedMarket" in labels
        assert "LiquidityPool" in labels

    def test_direct_execution_has_default_badge(self):
        meta = get_registry_metadata("execution_models", "direct")
        assert meta is not None
        assert meta.badges is not None
        assert any(b["label"] == "Default" for b in meta.badges)
