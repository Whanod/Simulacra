"""Tests for schema_for_dataclass + defaults_for_dataclass helpers and
the round-trip that every builtin's shipped defaults stays buildable
(BE-004).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from defi_sim.agents.arbitrageur import ArbitrageParams
from defi_sim.agents.informed import InformedParams
from defi_sim.agents.lp import LPParams
from defi_sim.agents.manipulator import ManipulatorParams
from defi_sim.agents.noise import NoiseParams
from defi_sim.engine.metadata import (
    defaults_for_dataclass,
    schema_and_defaults,
    schema_for_dataclass,
)
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
)


class TestSchemaForDataclass:
    def test_derives_schema_from_params_dataclass(self):
        schema = schema_for_dataclass(NoiseParams)
        assert schema["type"] == "object"
        properties = schema["properties"]
        assert "trade_min" in properties
        assert "trade_max" in properties
        assert "frequency" in properties

    def test_rejects_non_dataclass(self):
        class Plain:
            pass

        with pytest.raises(TypeError):
            schema_for_dataclass(Plain)

    def test_derives_defaults_from_dataclass(self):
        defaults = defaults_for_dataclass(NoiseParams)
        expected_instance = NoiseParams()
        for key, value in defaults.items():
            assert getattr(expected_instance, key) == value

    def test_schema_and_defaults_wrapper(self):
        schema, defaults = schema_and_defaults(ArbitrageParams)
        assert "properties" in schema
        assert defaults["min_edge_bps"] == 50


class TestBuiltinDefaultsRoundTrip:
    """For every registered builtin, load its defaults and confirm
    build_*(spec) succeeds. This guarantees shipped defaults never
    drift from the engine.
    """

    def test_clock_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["clocks"][0]:
            meta = get_registry_metadata("clocks", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_clock(ClockSpec(type=spec_type, params=dict(meta.defaults)))

    def test_ordering_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["orderings"][0]:
            meta = get_registry_metadata("orderings", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_ordering(OrderingSpec(type=spec_type, params=dict(meta.defaults)))

    def test_gas_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["gas_models"][0]:
            meta = get_registry_metadata("gas_models", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_gas_model(GasSpec(type=spec_type, params=dict(meta.defaults)))

    def test_information_filter_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["information_filters"][0]:
            meta = get_registry_metadata("information_filters", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_information_filter(
                InformationFilterSpec(type=spec_type, params=dict(meta.defaults))
            )

    def test_fee_model_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["fee_models"][0]:
            meta = get_registry_metadata("fee_models", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_fee_model(FeeModelSpec(type=spec_type, params=dict(meta.defaults)))

    def test_execution_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["execution_models"][0]:
            meta = get_registry_metadata("execution_models", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            build_execution_model(
                ExecutionSpec(type=spec_type, params=dict(meta.defaults))
            )

    def test_feed_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["feeds"][0]:
            meta = get_registry_metadata("feeds", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            # Composite feeds need nested feeds; skip builder invocation
            # since its spec shape requires more than a flat params block.
            if spec_type == "composite":
                continue
            build_feed(FeedSpec(type=spec_type, params=dict(meta.defaults)))

    def test_market_defaults_build(self):
        tokens = [
            TokenSpec(id="A", symbol="A", decimals=18),
            TokenSpec(id="B", symbol="B", decimals=18),
        ]
        for spec_type in _CATEGORY_TABLES["markets"][0]:
            meta = get_registry_metadata("markets", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            if spec_type == "world":
                # world markets are built from WorldSpec via a
                # separate isinstance branch in build_market; the
                # market-registry entry exists purely so the frontend
                # can surface the world-markets-graph special editor.
                continue
            if spec_type == "clob":
                # CLOB needs pairs — the empty-defaults case isn't a
                # runnable spec on its own, so we build a pair here.
                pair = {"base": {"id": "A", "symbol": "A", "decimals": 18},
                        "quote": {"id": "B", "symbol": "B", "decimals": 18}}
                build_market({
                    "type": "clob",
                    "pairs": [pair],
                })
                continue
            spec = MarketSpec(
                type=spec_type,
                tokens=list(tokens),
                params=dict(meta.defaults),
            )
            build_market(spec)

    def test_agent_defaults_build(self):
        for spec_type in _CATEGORY_TABLES["agents"][0]:
            meta = get_registry_metadata("agents", spec_type)
            assert meta is not None
            assert meta.defaults is not None
            spec = AgentSpec(
                type=spec_type,
                agent_id=f"{spec_type}-0",
                params=dict(meta.defaults),
            )
            build_agent(spec)
