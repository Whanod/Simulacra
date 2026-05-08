"""Tests for EntityMetadata and metadata-aware factory registration (BE-001)."""

from __future__ import annotations

import pytest

from defi_sim.engine.specs import (
    EntityMetadata,
    _CLOCK_FACTORIES,
    _CLOCK_META,
    build_clock,
    get_registry_metadata,
    iter_registry_categories,
    register_clock_factory,
)


class _ThrowawayClockSpec:
    def __init__(self) -> None:
        self.type = "throwaway"
        self.params: dict[str, object] = {}


def _factory(spec):  # noqa: ANN001 — test helper
    from defi_sim.core.clock import BlockClock

    return BlockClock(genesis=0, block_time=1, epoch_length=1)


class TestEntityMetadataDataclass:
    def test_defaults_are_optional_except_label(self):
        meta = EntityMetadata(label="Demo")
        assert meta.label == "Demo"
        assert meta.description == ""
        assert meta.schema is None
        assert meta.ui_schema is None
        assert meta.defaults is None
        assert meta.badges is None
        assert meta.builder_supported is True
        assert meta.examples is None
        assert meta.metadata is None

    def test_is_frozen(self):
        meta = EntityMetadata(label="Demo")
        with pytest.raises((AttributeError, TypeError)):
            meta.label = "Other"  # type: ignore[misc]


class TestRegistrationWithoutMetadata:
    def teardown_method(self) -> None:
        _CLOCK_FACTORIES.pop("bare", None)
        _CLOCK_META.pop("bare", None)

    def test_register_without_metadata_keeps_current_behavior(self):
        register_clock_factory("bare", _factory)
        assert "bare" in _CLOCK_FACTORIES
        assert "bare" not in _CLOCK_META
        assert get_registry_metadata("clocks", "bare") is None


class TestRegistrationWithMetadata:
    def teardown_method(self) -> None:
        _CLOCK_FACTORIES.pop("enriched", None)
        _CLOCK_META.pop("enriched", None)

    def test_metadata_is_stored_and_retrievable(self):
        meta = EntityMetadata(
            label="Enriched Clock",
            description="Test clock with metadata",
            schema={"type": "object", "properties": {}},
            defaults={"block_time": 1},
            builder_supported=True,
        )
        register_clock_factory("enriched", _factory, metadata=meta)
        retrieved = get_registry_metadata("clocks", "enriched")
        assert retrieved is meta
        assert retrieved.label == "Enriched Clock"
        assert retrieved.defaults == {"block_time": 1}


class TestCategoryTable:
    def test_known_categories_present_and_ordered(self):
        categories = iter_registry_categories()
        assert categories == [
            "markets",
            "agents",
            "clocks",
            "orderings",
            "gas_models",
            "fee_models",
            "feeds",
            "execution_models",
            "information_filters",
            "leader_schedules",
        ]

    def test_unknown_category_returns_none(self):
        assert get_registry_metadata("no_such_category", "anything") is None


class TestBuiltinsStillWork:
    def test_builtin_clock_still_builds_after_plumbing_change(self):
        clock = build_clock({"type": "block", "params": {"block_time": 2, "epoch_length": 1}})
        assert clock is not None
