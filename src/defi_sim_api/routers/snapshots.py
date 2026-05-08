"""Named snapshot catalog and fork endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.api import build_engine
from defi_sim.engine.config import CancellationToken
from defi_sim.engine.events import EventBus
from defi_sim.engine.snapshots import restore, snapshot

from defi_sim_api import schemas, state
from defi_sim_api.backend.runtime import persist_live_entry
from defi_sim_api.backend.serialization import market_type_from_spec
from defi_sim_api.backend.store import get_artifact_store

router = APIRouter(tags=["snapshots"])


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


@router.post(
    "/simulations/{simulation_id}/snapshots",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create a named durable snapshot from a live engine",
)
def create_named_snapshot(simulation_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = _get_entry(simulation_id)
    snapshot_id = state.new_id()
    record = get_artifact_store().create_named_snapshot(
        snapshot_id,
        run_id=entry.run_id,
        round_number=entry.engine.current_round,
        label=(body or {}).get("label"),
        blob=snapshot(entry.engine),
        simulation_id=simulation_id,
        source_run_id=entry.run_id,
    )
    persist_live_entry(entry)
    return record


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=dict[str, Any],
    summary="Get named snapshot metadata",
)
def get_named_snapshot(snapshot_id: str) -> dict[str, Any]:
    record = get_artifact_store().get_named_snapshot(snapshot_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id!r} not found")
    return record


@router.post(
    "/snapshots/{snapshot_id}/fork",
    response_model=schemas.EngineCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Fork a live engine from a stored named snapshot",
)
def fork_from_snapshot(snapshot_id: str) -> schemas.EngineCreatedResponse:
    store = get_artifact_store()
    snapshot_record = store.get_named_snapshot(snapshot_id)
    if snapshot_record is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id!r} not found")

    source_run_id = snapshot_record["run_id"]
    spec = store.get_run_spec(source_run_id)
    blob = store.get_named_snapshot_blob(snapshot_id)
    if spec is None or blob is None:
        raise HTTPException(status_code=404, detail="Snapshot artifacts are incomplete")

    run_id = state.new_id()
    cancel_token = CancellationToken()
    event_bus = EventBus(record_history=True, run_id=run_id)
    engine = build_engine(spec, event_bus=event_bus, cancel_token=cancel_token)
    restore(engine, blob)
    entry = state.EngineEntry(
        engine=engine,
        cancel_token=cancel_token,
        event_bus=event_bus,
        run_id=run_id,
        spec=spec,
    )
    state.store(run_id, entry)
    store.create_run(
        run_id,
        spec=spec,
        status="live",
        seed=spec.get("seed"),
        market_type=market_type_from_spec(spec),
        source="fork",
        simulation_id=run_id,
        source_run_id=source_run_id,
        source_snapshot_id=snapshot_id,
        current_round=engine.current_round,
        summary={
            "market_type": market_type_from_spec(spec),
            "seed": spec.get("seed"),
            "status": "live",
            "current_round": engine.current_round,
            "available_rounds": [],
            "agent_count": len(spec.get("agents", [])),
        },
    )
    persist_live_entry(entry)
    return schemas.EngineCreatedResponse(
        simulation_id=run_id,
        run_id=run_id,
        current_round=engine.current_round,
        is_complete=engine.is_complete,
    )
