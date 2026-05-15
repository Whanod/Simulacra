"""Simulation lifecycle endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from defi_sim.engine.api import build_engine, run_simulation
from defi_sim.engine.config import CancellationToken
from defi_sim.engine.events import EventBus
from defi_sim.engine.json import simulation_result_to_dict, to_jsonable
from defi_sim.engine.snapshots import restore, snapshot

from defi_sim_api import schemas, state
from defi_sim_api.auth import User, current_user
from defi_sim_api.backend.lighthouse_sizing import apply_lighthouse_sizing
from defi_sim_api.backend.runtime import create_live_run_record, persist_live_entry, persist_sync_run
from defi_sim_api.routers._ownership import assert_visible, owner_for_create

router = APIRouter(prefix="/simulations", tags=["simulations"])


def _get_entry(simulation_id: str, user: User) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    assert_visible(
        entry.owner_id,
        user,
        not_found_detail=f"Simulation {simulation_id!r} not found",
    )
    return entry


# ── Synchronous single-shot run ──────────────────────────────────────────

@router.post(
    "/run",
    response_model=schemas.SimulationResultResponse,
    status_code=status.HTTP_200_OK,
    summary="Run a full simulation synchronously",
)
def run(
    body: schemas.RunSpecSchema,
    user: User = Depends(current_user),
) -> schemas.SimulationResultResponse:
    spec_dict = body.model_dump(exclude_none=True)
    spec_dict = apply_lighthouse_sizing(spec_dict)
    run_id = state.new_id()
    event_bus = EventBus(record_history=True, run_id=run_id)
    result = run_simulation(spec_dict, event_bus=event_bus)
    persist_sync_run(
        run_id,
        spec=spec_dict,
        result=result,
        events=event_bus.history,
        owner_id=owner_for_create(user),
    )
    return schemas.SimulationResultResponse(
        run_id=run_id,
        result=simulation_result_to_dict(result),
    )


# ── Engine lifecycle (build → step → status → cancel) ────────────────────

@router.post(
    "/build",
    response_model=schemas.EngineCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Build an engine from a RunSpec without running it",
)
def build(
    body: schemas.RunSpecSchema,
    user: User = Depends(current_user),
) -> schemas.EngineCreatedResponse:
    spec_dict = body.model_dump(exclude_none=True)
    spec_dict = apply_lighthouse_sizing(spec_dict)
    cancel_token = CancellationToken()
    sim_id = state.new_id()
    event_bus = EventBus(record_history=True, run_id=sim_id)
    engine = build_engine(spec_dict, event_bus=event_bus, cancel_token=cancel_token)
    owner = owner_for_create(user)
    state.store(
        sim_id,
        state.EngineEntry(
            engine=engine,
            cancel_token=cancel_token,
            event_bus=event_bus,
            run_id=sim_id,
            spec=spec_dict,
            owner_id=owner,
        ),
    )
    create_live_run_record(sim_id, spec_dict, owner_id=owner)
    return schemas.EngineCreatedResponse(
        simulation_id=sim_id,
        run_id=sim_id,
        current_round=engine.current_round,
        is_complete=engine.is_complete,
    )


@router.post(
    "/{simulation_id}/step",
    response_model=schemas.StepResponse,
    summary="Advance the simulation by one round",
)
def step(
    simulation_id: str,
    user: User = Depends(current_user),
) -> schemas.StepResponse:
    entry = _get_entry(simulation_id, user)
    engine = entry.engine
    if engine.is_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Simulation already complete",
        )
    try:
        round_snap = engine.step()
    except StopIteration:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Simulation already complete",
        )
    persist_live_entry(entry)
    return schemas.StepResponse(
        simulation_id=simulation_id,
        run_id=entry.run_id,
        round=round_snap.round,
        snapshot=to_jsonable(round_snap),
        is_complete=engine.is_complete,
    )


@router.get(
    "/{simulation_id}/status",
    response_model=schemas.SimulationStatusResponse,
    summary="Get engine status",
)
def get_status(
    simulation_id: str,
    user: User = Depends(current_user),
) -> schemas.SimulationStatusResponse:
    entry = _get_entry(simulation_id, user)
    engine = entry.engine
    return schemas.SimulationStatusResponse(
        simulation_id=simulation_id,
        run_id=entry.run_id,
        current_round=engine.current_round,
        is_complete=engine.is_complete,
        cancelled=entry.cancel_token.is_cancelled(),
    )


@router.post(
    "/{simulation_id}/cancel",
    response_model=schemas.CancelResponse,
    summary="Cancel a running simulation",
)
def cancel(
    simulation_id: str,
    user: User = Depends(current_user),
) -> schemas.CancelResponse:
    entry = _get_entry(simulation_id, user)
    entry.cancel_token.cancel("cancelled via API")
    persist_live_entry(entry, status="cancelled")
    return schemas.CancelResponse(
        simulation_id=simulation_id,
        cancelled=True,
        reason="cancelled via API",
    )


@router.delete(
    "/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a simulation engine from memory",
)
def delete(
    simulation_id: str,
    user: User = Depends(current_user),
) -> None:
    # Visibility check before mutating state — never let a cross-owner
    # caller pop another user's engine.
    _get_entry(simulation_id, user)
    removed = state.remove(simulation_id)
    if removed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    persist_live_entry(removed, status="completed" if removed.engine.is_complete else "deleted")


@router.get(
    "",
    response_model=list[str],
    summary="List active simulation IDs",
)
def list_simulations(user: User = Depends(current_user)) -> list[str]:
    # In-memory engine list filtered by owner when auth is enforced; open
    # mode returns all ids for the legacy local-dev contract.
    from defi_sim_api.auth import auth_enforced

    if not auth_enforced():
        return state.list_ids()
    return [
        sim_id for sim_id in state.list_ids()
        if (entry := state.get(sim_id)) is not None
        and (entry.owner_id is None or entry.owner_id == user.id)
    ]


# ── Snapshot / restore ────────────────────────────────────────────────────

@router.post(
    "/{simulation_id}/snapshot",
    response_model=schemas.SnapshotResponse,
    summary="Serialize engine state to a portable snapshot",
)
def take_snapshot(
    simulation_id: str,
    user: User = Depends(current_user),
) -> schemas.SnapshotResponse:
    entry = _get_entry(simulation_id, user)
    blob = snapshot(entry.engine)
    return schemas.SnapshotResponse(
        simulation_id=simulation_id,
        snapshot_bytes_hex=blob.hex(),
    )


@router.post(
    "/{simulation_id}/restore",
    response_model=schemas.RestoreResponse,
    summary="Restore engine state from a portable snapshot",
)
def restore_snapshot(
    simulation_id: str,
    body: schemas.RestoreRequest,
    user: User = Depends(current_user),
) -> schemas.RestoreResponse:
    entry = _get_entry(simulation_id, user)
    blob = bytes.fromhex(body.snapshot_bytes_hex)
    restore(entry.engine, blob)
    entry.completion_event_emitted = False
    persist_live_entry(entry, status="completed" if entry.engine.is_complete else "live")
    return schemas.RestoreResponse(
        simulation_id=simulation_id,
        restored=True,
        current_round=entry.engine.current_round,
    )
