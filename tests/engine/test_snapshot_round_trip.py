"""Snapshot round-trip regression for the slot execution pipeline (PRD 1.0 DoD).

PRD line 223 names this file by path:
``tests/engine/test_snapshot_round_trip.py::test_solana_like_with_serial_scheduler_round_trips``.

The contract:
1. A SolanaLikeExecution engine snapshots and restores cleanly with the new
   ``scheduler`` field present and parsed back to ``SerialScheduler``.
2. A snapshot generated before the ``scheduler`` field existed must still load
   and default to ``SerialScheduler`` (PRD line 193: "the deserializer accepts
   a missing scheduler key").
"""

from __future__ import annotations

import copy

from defi_sim._compat import msgpack
from defi_sim.core.types import decode_msgpack_value, encode_msgpack_value
from defi_sim.engine.api import build_engine
from defi_sim.engine.execution import (
    SolanaLikeExecution,
    deserialize_execution_model,
    serialize_execution_model,
)
from defi_sim.engine.scheduler import SerialScheduler
from defi_sim.engine.snapshots import restore, snapshot


SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "noise-2",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
    ],
    "num_rounds": 4,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def test_solana_like_with_serial_scheduler_round_trips() -> None:
    """Run a few rounds, snapshot, restore into a fresh engine, and assert
    state equality + that the restored model carries the same scheduler
    discriminator. PRD US-003 step 4 flipped the default to
    ``PriorityScheduler``; the round-trip preserves whichever class the
    builder defaulted to."""
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    assert isinstance(engine._execution_model, SolanaLikeExecution)
    original_scheduler_cls = type(engine._execution_model._scheduler)
    engine.run()

    blob = snapshot(engine)

    restored = build_engine(copy.deepcopy(SOLANA_SPEC))
    restore(restored, blob)

    assert restored._current_round == engine._current_round
    assert isinstance(restored._execution_model, SolanaLikeExecution)
    assert isinstance(restored._execution_model._scheduler, original_scheduler_cls)

    original_balances = {a.agent_id: dict(a.state.balances) for a in engine._agents}
    restored_balances = {a.agent_id: dict(a.state.balances) for a in restored._agents}
    assert original_balances == restored_balances


def test_legacy_snapshot_without_scheduler_field_defaults_to_serial() -> None:
    """A snapshot taken before the scheduler field existed must still load."""
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()
    blob = snapshot(engine)

    decoded = decode_msgpack_value(msgpack.unpackb(blob, raw=False))
    # Strip the new field to simulate an artifact predating PRD 1.0.
    decoded["execution_model"].pop("scheduler", None)
    legacy_blob = msgpack.packb(encode_msgpack_value(decoded), use_bin_type=True)

    restored = build_engine(copy.deepcopy(SOLANA_SPEC))
    restore(restored, legacy_blob)

    assert isinstance(restored._execution_model, SolanaLikeExecution)
    assert isinstance(restored._execution_model._scheduler, SerialScheduler)


def test_serialize_execution_model_includes_scheduler_field() -> None:
    """Direct serializer surface still includes the scheduler descriptor and
    round-trips through deserialize_execution_model. PRD US-003 step 4
    flipped the ``SolanaLikeExecution`` default to ``PriorityScheduler``."""
    from defi_sim.engine.scheduler import PriorityScheduler

    model = SolanaLikeExecution()
    data = serialize_execution_model(model)
    assert data.get("scheduler") == {"type": "priority"}
    restored = deserialize_execution_model(data)
    assert isinstance(restored, SolanaLikeExecution)
    assert isinstance(restored._scheduler, PriorityScheduler)
