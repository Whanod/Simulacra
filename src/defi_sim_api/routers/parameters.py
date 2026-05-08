"""Parameter store endpoints for live engines."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.events import Event, EventType
from defi_sim.engine.parameters import ScheduledChange

from defi_sim_api import schemas, state
from defi_sim_api.backend.runtime import persist_live_entry

router = APIRouter(prefix="/simulations", tags=["parameters"])


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


@router.get(
    "/{simulation_id}/parameters",
    response_model=schemas.ParameterStoreResponse,
    summary="Get the full parameter store state",
)
def get_parameters(simulation_id: str) -> schemas.ParameterStoreResponse:
    entry = _get_entry(simulation_id)
    store = entry.engine._parameters
    data = store.to_dict()
    return schemas.ParameterStoreResponse(
        params=data["params"],
        pending=data["pending"],
        history=data["history"],
    )


@router.put(
    "/{simulation_id}/parameters",
    response_model=dict[str, object],
    summary="Set a parameter value immediately",
)
def set_parameter(simulation_id: str, body: schemas.ParameterSetRequest) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    store = entry.engine._parameters
    old = store.set(body.key, body.value, round=entry.engine.current_round)
    entry.event_bus.emit(
        Event(
            type=EventType.PARAMETER_CHANGED,
            round=entry.engine.current_round,
            timestamp=entry.engine._clock.timestamp(entry.engine.current_round),  # noqa: SLF001
            data={
                "key": body.key,
                "old_value": old,
                "new_value": body.value,
                "source": "api",
                "proposal_id": None,
                "proposed_by": None,
            },
        )
    )
    persist_live_entry(entry)
    return {"key": body.key, "old_value": old, "new_value": body.value}


@router.post(
    "/{simulation_id}/parameters/schedule",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a parameter change for a future round",
)
def schedule_parameter(
    simulation_id: str,
    body: schemas.ScheduledChangeRequest,
) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    store = entry.engine._parameters
    change = ScheduledChange(
        key=body.key,
        value=body.value,
        execute_at_round=body.execute_at_round,
        proposed_by=body.proposed_by,
        proposal_id=body.proposal_id,
    )
    store.schedule(change)
    persist_live_entry(entry)
    return {"scheduled": True, "key": body.key, "execute_at_round": body.execute_at_round}


# IMPORTANT: /history must come BEFORE /{key} to avoid FastAPI matching "history" as a key
@router.get(
    "/{simulation_id}/parameters/history",
    response_model=dict[str, object],
    summary="Get parameter change history",
)
def get_parameter_history(
    simulation_id: str,
    key: str | None = None,
) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    store = entry.engine._parameters
    history = store.get_history(key)
    return {
        "history": [
            {"round": r, "key": k, "old_value": o, "new_value": n}
            for r, k, o, n in history
        ]
    }


@router.get(
    "/{simulation_id}/parameters/{key}",
    response_model=dict[str, object],
    summary="Get a single parameter value",
)
def get_parameter(simulation_id: str, key: str) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    store = entry.engine._parameters
    value = store.get(key)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Parameter {key!r} not found",
        )
    return {"key": key, "value": value}
