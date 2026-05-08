from __future__ import annotations

import json

from defi_sim.core.clock import BlockClock
from defi_sim.engine.api import build_engine, run_simulation
from defi_sim.engine.config import CancellationToken
from defi_sim.engine.json import BIGINT_MARKER, simulation_result_to_dict, simulation_result_to_json
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.snapshots import restore, snapshot
from defi_sim.engine.specs import RunSpec, WorldSpec, build_market, build_simulation_config
from defi_sim.engine.gas import FixedCost
from defi_sim.engine.ordering import PriorityOrdering
from defi_sim.engine.world import World


SAFE_TEST_BALANCE = 10**16


def _run_spec_dict() -> dict[str, object]:
    return {
        "market": {
            "type": "cfamm",
            "tokens": [
                {"id": "YES", "symbol": "YES", "decimals": 9},
                {"id": "NO", "symbol": "NO", "decimals": 9},
            ],
            "fee_model": {
                "type": "flat",
                "params": {
                    "trade_fee_bps": 25,
                    "split_config": {"lp": 7000, "protocol": 3000},
                },
            },
            "params": {
                "initial_liquidity": 1_000_000,
                "collateral_token": "COLLATERAL",
            },
        },
        "agents": [
            {
                "type": "noise",
                "agent_id": "noise-1",
                "params": {
                    "collateral": "COLLATERAL",
                    "frequency": 0.0,
                },
                "initial_balances": {
                    "COLLATERAL": {BIGINT_MARKER: str(SAFE_TEST_BALANCE)},
                },
            }
        ],
        "num_rounds": 2,
        "snapshot_interval": 1,
        "seed": 11,
        "retain_snapshots": True,
        "numeric_mode": "fixed",
        "clock": {
            "type": "block",
            "params": {"genesis": 1_000, "block_time": 12, "epoch_length": 2},
        },
        "ordering": {
            "type": "priority",
        },
        "gas_model": {
            "type": "fixed",
            "params": {"cost_per_action": 7},
        },
        "information_filter": {
            "type": "delayed_information",
            "params": {"delays": {"noise": 1}},
        },
        "default_fee_model": {
            "type": "time_weighted",
            "params": {"base_bps": 5, "max_bps": 15},
        },
    }


def _world_run_spec_dict() -> dict[str, object]:
    return {
        "market": {
            "type": "world",
            "markets": {
                "amm": {
                    "type": "cfamm",
                    "tokens": [
                        {"id": "YES", "symbol": "YES", "decimals": 9},
                        {"id": "NO", "symbol": "NO", "decimals": 9},
                    ],
                    "params": {
                        "initial_liquidity": 1_000_000,
                        "collateral_token": "COLLATERAL",
                    },
                },
                "book": {
                    "type": "clob",
                    "pairs": [
                        {
                            "base": {"id": "ETH", "symbol": "ETH", "decimals": 9},
                            "quote": {"id": "USDC", "symbol": "USDC", "decimals": 9},
                        }
                    ],
                },
            },
        },
        "agents": [
            {
                "type": "noise",
                "agent_id": "observer",
                "params": {
                    "collateral": "COLLATERAL",
                    "frequency": 0.0,
                },
                "initial_balances": {
                    "COLLATERAL": 1_000_000,
                },
            }
        ],
        "num_rounds": 2,
        "snapshot_interval": 1,
        "seed": 21,
        "retain_snapshots": True,
    }


def test_run_spec_from_dict_decodes_bigints_and_builds_runtime_config():
    spec = RunSpec.from_dict(_run_spec_dict())

    assert spec.agents[0].initial_balances["COLLATERAL"] == SAFE_TEST_BALANCE

    config = build_simulation_config(spec)
    assert isinstance(config.clock, BlockClock)
    assert isinstance(config.execution_model._ordering, PriorityOrdering)
    assert isinstance(config.execution_model._cost_model, FixedCost)
    assert config.execution_model.cost_token(None) == "COLLATERAL"
    assert config.information_filter is not None
    assert config.default_fee_model is not None


def test_run_simulation_executes_single_run_from_json_spec():
    result = run_simulation(_run_spec_dict())

    assert result.num_rounds == 2
    assert result.num_rounds_executed == 2
    assert result.seed == 11
    assert result.round_snapshots[0].timestamp == 1_012
    assert result.round_snapshots[1].timestamp == 1_024
    assert result.agent_final_states["noise-1"].balances["COLLATERAL"] == SAFE_TEST_BALANCE


def test_simulation_result_serializes_full_result_graph_to_json():
    result = run_simulation(_run_spec_dict())

    payload = simulation_result_to_dict(result)
    assert payload["__type__"] == "SimulationResult"
    assert payload["round_snapshots"][0]["market_state"]["__type__"] == "AmmSnapshot"
    assert payload["agent_final_states"]["noise-1"]["balances"]["COLLATERAL"] == {
        BIGINT_MARKER: str(SAFE_TEST_BALANCE)
    }

    payload_json = simulation_result_to_json(result, indent=2)
    decoded = json.loads(payload_json)
    assert decoded["agent_final_states"]["noise-1"]["balances"]["COLLATERAL"][BIGINT_MARKER] == str(
        SAFE_TEST_BALANCE
    )


def test_cancellation_token_stops_simulation_without_using_early_stop():
    token = CancellationToken()
    engine = build_engine(_run_spec_dict(), cancel_token=token)
    engine._config.progress_callback = lambda current, total: token.cancel("cancelled by test") if current == 1 else None

    result = engine.run()

    assert result.cancelled is True
    assert result.stopped_early is True
    assert result.stop_reason == "cancelled by test"
    assert result.num_rounds_executed == 1


def test_snapshot_roundtrip_preserves_json_built_fee_models():
    engine = build_engine(_run_spec_dict())
    blob = snapshot(engine)

    restored_engine = build_engine(_run_spec_dict())
    restore(restored_engine, blob)

    assert restored_engine._market.fee_model is not None
    assert restored_engine._market.fee_model.keywords["trade_fee_bps"] == 25
    assert restored_engine._config.default_fee_model is not None
    assert restored_engine._config.default_fee_model.keywords["base_bps"] == 5


def test_build_market_accepts_market_spec_objects():
    spec = RunSpec.from_dict(_run_spec_dict())
    market = build_market(spec.market)

    assert market.market_type == "cfamm"


def test_world_run_spec_from_dict_builds_world_spec():
    spec = RunSpec.from_dict(_world_run_spec_dict())

    assert isinstance(spec.market, WorldSpec)
    assert set(spec.market.markets) == {"amm", "book"}

    market = build_market(spec.market)
    assert isinstance(market, World)
    assert set(market.markets) == {"amm", "book"}


def test_run_simulation_executes_world_spec_and_returns_world_snapshots():
    result = run_simulation(_world_run_spec_dict())

    assert result.num_rounds_executed == 2
    assert result.round_snapshots[0].all_market_states is not None
    assert set(result.round_snapshots[0].all_market_states) == {"amm", "book"}
    assert result.round_snapshots[0].market_state is None
    assert len(result.price_history) == 2
    assert result.price_history[0] == result.price_history[1]
    assert set(result.price_history[0]) == {"amm:YES", "amm:NO", "book:ETH"}
    assert result.price_history[0]["book:ETH"] == 0
    assert result.price_history[0]["amm:YES"] > 0
    assert result.price_history[0]["amm:NO"] > 0
