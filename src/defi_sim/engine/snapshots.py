"""State checkpoint / restore (msgpack, versioned)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from defi_sim.core.agent import (
    _deserialize_snapshot_view,
    _serialize_snapshot_view,
    deserialize_information_filter,
    serialize_information_filter,
)
from defi_sim._compat import msgpack
from defi_sim.core.market import (
    deserialize_callable_ref,
    get_market_registry,
    serialize_callable_ref,
)
from defi_sim.core.types import (
    AgentState,
    BundleOutcome,
    RoundSnapshot,
    ValidatorEpochRevenue,
    decode_msgpack_value,
    encode_msgpack_value,
)
from defi_sim.engine.execution import (
    attach_ordering_rng,
    attach_submission_rng,
    deserialize_execution_model,
    serialize_execution_model,
)
from defi_sim.engine.feeds import deserialize_feed, serialize_feed
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.world import World

if TYPE_CHECKING:
    from defi_sim.engine.simulation import SimulationEngine

SNAPSHOT_VERSION: int = 1


def _normalize_fee_splits(raw: object) -> dict:
    """Accept both pre-token (dict[str, Numeric]) and token-aware
    (dict[str, dict[str, Numeric]]) shapes so snapshots taken before the
    token-aware migration still restore without errors. Scalars are
    wrapped under an empty-string token key so cumulative totals stay
    correct even though the original token identity was never recorded.
    """
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for destination, value in raw.items():
        if isinstance(value, dict):
            normalized[destination] = dict(value)
        else:
            normalized[destination] = {"": value}
    return normalized


def _serialize_bundle_outcome(outcome: BundleOutcome) -> dict[str, object]:
    return {
        "slot": outcome.slot,
        "bundle_index": outcome.bundle_index,
        "status": outcome.status,
        "tip_lamports": outcome.tip_lamports,
        "validator_revenue_lamports": outcome.validator_revenue_lamports,
        "stake_pool_revenue_lamports": outcome.stake_pool_revenue_lamports,
        "alt_ids": list(outcome.alt_ids),
        "num_txs": outcome.num_txs,
        "total_cu": outcome.total_cu,
        "failed_at_index": outcome.failed_at_index,
        "drop_reason": outcome.drop_reason,
    }


def _deserialize_bundle_outcome(data: dict[str, object]) -> BundleOutcome:
    return BundleOutcome(
        slot=int(data.get("slot", 0)),
        bundle_index=int(data.get("bundle_index", 0)),
        status=data.get("status", "dropped"),
        tip_lamports=int(data.get("tip_lamports", 0)),
        validator_revenue_lamports=int(data.get("validator_revenue_lamports", 0)),
        stake_pool_revenue_lamports=int(data.get("stake_pool_revenue_lamports", 0)),
        alt_ids=tuple(data.get("alt_ids", []) or []),
        num_txs=int(data.get("num_txs", 0)),
        total_cu=int(data.get("total_cu", 0)),
        failed_at_index=data.get("failed_at_index"),
        drop_reason=data.get("drop_reason"),
    )


def _serialize_validator_epoch_revenue(entry: ValidatorEpochRevenue) -> dict[str, object]:
    return {
        "epoch": entry.epoch,
        "pubkey": entry.pubkey,
        "client": entry.client,
        "validator_revenue_lamports": entry.validator_revenue_lamports,
        "stake_pool_revenue_lamports": entry.stake_pool_revenue_lamports,
    }


def _deserialize_validator_epoch_revenue(data: dict[str, object]) -> ValidatorEpochRevenue:
    return ValidatorEpochRevenue(
        epoch=int(data.get("epoch", 0)),
        pubkey=str(data.get("pubkey", "")),
        client=data.get("client", "jito_solana"),
        validator_revenue_lamports=int(data.get("validator_revenue_lamports", 0)),
        stake_pool_revenue_lamports=int(data.get("stake_pool_revenue_lamports", 0)),
    )


def _serialize_snapshot_metrics(metrics: dict[str, object]) -> dict[str, object]:
    serialized: dict[str, object] = {}
    validator_revenue = metrics.get("validator_revenue")
    if isinstance(validator_revenue, dict):
        serialized["validator_revenue"] = [
            {
                "epoch": int(epoch),
                "entries": [
                    _serialize_validator_epoch_revenue(entry)
                    for entry in epoch_bucket.values()
                ],
            }
            for epoch, epoch_bucket in validator_revenue.items()
        ]
    return serialized


def _deserialize_snapshot_metrics(data: object) -> dict[str, object]:
    metrics: dict[str, object] = {}
    if not isinstance(data, dict):
        return metrics
    validator_revenue_blob = data.get("validator_revenue")
    if isinstance(validator_revenue_blob, list):
        validator_revenue: dict[int, dict[str, ValidatorEpochRevenue]] = {}
        for epoch_blob in validator_revenue_blob:
            if not isinstance(epoch_blob, dict):
                continue
            epoch = int(epoch_blob.get("epoch", 0))
            bucket: dict[str, ValidatorEpochRevenue] = {}
            for entry_blob in epoch_blob.get("entries", []) or []:
                if not isinstance(entry_blob, dict):
                    continue
                entry = _deserialize_validator_epoch_revenue(entry_blob)
                bucket[entry.pubkey] = entry
            validator_revenue[epoch] = bucket
        metrics["validator_revenue"] = validator_revenue
    return metrics


def _serialize_round_snapshot(snapshot: RoundSnapshot) -> dict[str, object]:
    return {
        "round": snapshot.round,
        "timestamp": snapshot.timestamp,
        "epoch": snapshot.epoch,
        "agent_states": [
            {"agent_id": agent_id, "data": agent_state.to_bytes()}
            for agent_id, agent_state in snapshot.agent_states.items()
        ],
        "market_state": (
            _serialize_snapshot_view(snapshot.market_state)
            if snapshot.market_state is not None else None
        ),
        "all_market_states": (
            _serialize_snapshot_view(snapshot.all_market_states)
            if snapshot.all_market_states is not None else None
        ),
        "current_slot": snapshot.current_slot,
        "current_leader": snapshot.current_leader,
        "bundle_outcomes": [
            _serialize_bundle_outcome(outcome) for outcome in snapshot.bundle_outcomes
        ],
        "metrics": _serialize_snapshot_metrics(snapshot.metrics),
    }


def _deserialize_round_snapshot(data: dict[str, object]) -> RoundSnapshot:
    agent_states = {
        entry["agent_id"]: AgentState.from_bytes(entry["data"])
        for entry in data.get("agent_states", [])
    }
    market_state = data.get("market_state")
    all_market_states = data.get("all_market_states")
    return RoundSnapshot(
        round=data.get("round", 0),
        timestamp=data.get("timestamp", 0),
        epoch=data.get("epoch", 0),
        agent_states=agent_states,
        market_state=(
            _deserialize_snapshot_view(market_state)
            if market_state is not None else None
        ),
        all_market_states=(
            _deserialize_snapshot_view(all_market_states)
            if all_market_states is not None else None
        ),
        current_slot=data.get("current_slot"),
        current_leader=data.get("current_leader"),
        bundle_outcomes=[
            _deserialize_bundle_outcome(entry)
            for entry in (data.get("bundle_outcomes") or [])
        ],
        metrics=_deserialize_snapshot_metrics(data.get("metrics")),
    )


def snapshot(engine: "SimulationEngine") -> bytes:
    """Serialize full engine state using msgpack with versioned header."""
    market = engine._market
    market_type = "world" if isinstance(market, World) else (market.market_type if hasattr(market, "market_type") else "unknown")

    header = {
        "version": SNAPSHOT_VERSION,
        "market_type": market_type,
        "round": engine.current_round,
        "timestamp": engine._clock.timestamp(engine.current_round),
    }

    agent_bytes = [
        {"agent_id": agent.agent_id, "data": agent.state.to_bytes()}
        for agent in engine._agents
    ]

    if isinstance(market, World):
        market_blob = {
            "markets": [
                {
                    "name": name,
                    "market_type": child.market_type,
                    "data": child.to_bytes(),
                }
                for name, child in market.markets.items()
            ],
        }
    else:
        market_blob = market.to_bytes() if hasattr(market, "to_bytes") else b""

    data = {
        "header": header,
        "market": market_blob,
        "agents": agent_bytes,
        "agent_rngs": [
            {"agent_id": agent_id, "state": rng.bit_generator.state}
            for agent_id, rng in engine._agent_rngs.items()
        ],
        "rng": {
            "agent": engine._agent_rng.bit_generator.state,
            "ordering": engine._ordering_rng.bit_generator.state,
            "feed": engine._feed_rng.bit_generator.state,
            "engine": engine._engine_rng.bit_generator.state,
            "submission": engine._submission_rng.bit_generator.state,
        },
        "parameters": engine._parameters.to_dict(),
        "execution_model": serialize_execution_model(engine._execution_model),
        "default_fee_model": serialize_callable_ref(engine._config.default_fee_model),
        "information_filter": serialize_information_filter(engine._info_filter),
        "feeds": [
            serialize_feed(feed)
            for feed in (engine._config.feeds or [])
        ],
        "stopped_early": engine._stopped_early,
        "cancelled": engine._cancelled,
        "stop_reason": engine._stop_reason,
        "started": engine._started,
        "fee_destination_balances": engine._fee_destination_balances,
        "last_feed_prices": engine._last_feed_prices,
        "price_history": list(engine._price_history),
        "fee_history": [
            {dest: dict(tokens) for dest, tokens in splits.items()}
            for splits in engine._fee_history
        ],
        "round_fee_splits": {
            dest: dict(tokens) for dest, tokens in engine._round_fee_splits.items()
        },
        "round_snapshots": [
            _serialize_round_snapshot(round_snapshot)
            for round_snapshot in engine._snapshots
        ],
    }

    return msgpack.packb(encode_msgpack_value(data), use_bin_type=True)


def restore(engine: "SimulationEngine", data: bytes) -> None:
    """Restore engine from snapshot."""
    d = decode_msgpack_value(
        msgpack.unpackb(data, raw=False, strict_map_key=False)
    )
    header = d["header"]

    registry = get_market_registry()
    market_type = header["market_type"]

    if market_type == "world":
        restored_world = World()
        for entry in d["market"].get("markets", []):
            cls = registry[entry["market_type"]]
            restored_world.add_market(entry["name"], cls.from_bytes(entry["data"]))
        restored_world.attach_event_bus(
            engine._bus,
            round_provider=lambda: engine._current_round,
            timestamp_provider=lambda: engine._clock.timestamp(engine._current_round),
        )
        engine._market = restored_world
        engine._is_world = True
    elif market_type in registry:
        cls = registry[market_type]
        engine._market = cls.from_bytes(d["market"])
        engine._is_world = False

    agent_entries = d.get("agents", [])
    if isinstance(agent_entries, dict):
        restored_agents = {key: AgentState.from_bytes(value) for key, value in agent_entries.items()}
        for agent in engine._agents:
            key = str(agent.agent_id)
            if key in restored_agents:
                agent.state = restored_agents[key]
    else:
        restored_agents = {entry["agent_id"]: AgentState.from_bytes(entry["data"]) for entry in agent_entries}
        for agent in engine._agents:
            if agent.agent_id in restored_agents:
                agent.state = restored_agents[agent.agent_id]

    engine._current_round = header["round"]
    rng_state = d["rng"]
    engine._agent_rng.bit_generator.state = rng_state["agent"]
    engine._ordering_rng.bit_generator.state = rng_state["ordering"]
    engine._feed_rng.bit_generator.state = rng_state["feed"]
    engine._engine_rng.bit_generator.state = rng_state["engine"]
    if "submission" in rng_state:
        engine._submission_rng.bit_generator.state = rng_state["submission"]
    for entry in d.get("agent_rngs", []):
        agent_id = entry["agent_id"]
        if agent_id in engine._agent_rngs:
            engine._agent_rngs[agent_id].bit_generator.state = entry["state"]
            agent = next((candidate for candidate in engine._agents if candidate.agent_id == agent_id), None)
            if agent is not None and hasattr(agent, "_rng"):
                agent._rng = engine._agent_rngs[agent_id]
    engine._parameters = ParameterStore.from_dict(d.get("parameters", {}))
    engine._execution_model = deserialize_execution_model(d["execution_model"])
    attach_ordering_rng(engine._execution_model, engine._ordering_rng)
    attach_submission_rng(engine._execution_model, engine._submission_rng)
    engine._config.execution_model = engine._execution_model
    engine._config.default_fee_model = deserialize_callable_ref(d.get("default_fee_model"))
    engine._info_filter = deserialize_information_filter(d["information_filter"])
    engine._config.information_filter = engine._info_filter
    engine._config.feeds = [
        deserialize_feed(feed)
        for feed in d.get("feeds", [])
    ]
    engine._stopped_early = d.get("stopped_early", False)
    engine._cancelled = d.get("cancelled", False)
    engine._stop_reason = d.get("stop_reason")
    engine._started = d.get("started", engine._current_round > 0)
    engine._fee_destination_balances = d.get("fee_destination_balances", {})
    engine._last_feed_prices = d.get("last_feed_prices")
    engine._price_history = list(d.get("price_history", []))
    engine._fee_history = [
        _normalize_fee_splits(splits) for splits in d.get("fee_history", [])
    ]
    engine._round_fee_splits = _normalize_fee_splits(d.get("round_fee_splits", {}))
    engine._snapshots = [
        _deserialize_round_snapshot(round_snapshot)
        for round_snapshot in d.get("round_snapshots", [])
    ]
