"""Durable run retrieval, comparison, and snapshot catalog endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from defi_sim_api.backend.serialization import (
    agent_summary,
    agent_timeline_from_rounds,
    filter_events,
    price_summary,
)
from defi_sim_api.backend.store import get_artifact_store

router = APIRouter(prefix="/runs", tags=["runs"])


def _require_run(run_id: str) -> dict[str, Any]:
    run = get_artifact_store().get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id!r} not found",
        )
    return run


def _flatten_diff(prefix: str, left: Any, right: Any, out: dict[str, dict[str, Any]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        for key in sorted(keys):
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten_diff(next_prefix, left.get(key), right.get(key), out)
        return
    if left != right:
        out[prefix or "value"] = {"left": left, "right": right}


@router.post(
    "/compare",
    response_model=dict[str, Any],
    summary="Compare two durable runs",
)
def compare_runs(body: dict[str, str]) -> dict[str, Any]:
    left_run_id = body.get("left_run_id")
    right_run_id = body.get("right_run_id")
    if not left_run_id or not right_run_id:
        raise HTTPException(status_code=422, detail="left_run_id and right_run_id are required")

    store = get_artifact_store()
    _require_run(left_run_id)
    _require_run(right_run_id)
    left_spec = store.get_run_spec(left_run_id) or {}
    right_spec = store.get_run_spec(right_run_id) or {}
    left_result = store.get_run_result(left_run_id) or {}
    right_result = store.get_run_result(right_run_id) or {}

    spec_diff: dict[str, dict[str, Any]] = {}
    _flatten_diff("", left_spec, right_spec, spec_diff)

    metric_keys = ["num_rounds", "num_rounds_executed", "seed", "stopped_early", "cancelled"]
    metric_diff: dict[str, dict[str, Any]] = {}
    for key in metric_keys:
        left_value = left_result.get(key)
        right_value = right_result.get(key)
        metric_diff[key] = {
            "left": left_value,
            "right": right_value,
            "delta": (right_value - left_value)
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float))
            else None,
        }

    metadata_diff: dict[str, dict[str, Any]] = {}
    _flatten_diff("", left_result.get("metadata", {}), right_result.get("metadata", {}), metadata_diff)

    left_prices = price_summary(left_result)
    right_prices = price_summary(right_result)
    price_delta: dict[str, dict[str, Any]] = {}
    for key in sorted(set(left_prices) | set(right_prices)):
        left_entry = left_prices.get(key, {})
        right_entry = right_prices.get(key, {})
        left_end = left_entry.get("end")
        right_end = right_entry.get("end")
        price_delta[key] = {
            "left": left_entry,
            "right": right_entry,
            "delta_end": (right_end - left_end)
            if isinstance(left_end, (int, float)) and isinstance(right_end, (int, float))
            else None,
        }

    left_agents = agent_summary(left_result)
    right_agents = agent_summary(right_result)
    agent_delta: dict[str, dict[str, Any]] = {}
    for key in sorted(set(left_agents) | set(right_agents)):
        left_entry = left_agents.get(key, {})
        right_entry = right_agents.get(key, {})
        agent_delta[key] = {
            "left": left_entry,
            "right": right_entry,
            "delta_realized_pnl": (
                right_entry.get("realized_pnl") - left_entry.get("realized_pnl")
                if isinstance(left_entry.get("realized_pnl"), (int, float))
                and isinstance(right_entry.get("realized_pnl"), (int, float))
                else None
            ),
        }

    return {
        "left_run_id": left_run_id,
        "right_run_id": right_run_id,
        "equal": not spec_diff and all(item["delta"] in {0, None} for item in metric_diff.values()),
        "spec_diff": spec_diff,
        "metric_diff": metric_diff,
        "metadata_diff": metadata_diff,
        "price_summary_delta": price_delta,
        "agent_summary_delta": agent_delta,
    }


@router.get(
    "",
    response_model=dict[str, Any],
    summary="List durable runs",
)
def list_runs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    store = get_artifact_store()
    runs = store.list_runs(limit=limit, offset=offset)
    return {
        "runs": runs,
        "count": store.count_runs(),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{run_id}",
    response_model=dict[str, Any],
    summary="Get durable run metadata",
)
def get_run(run_id: str) -> dict[str, Any]:
    run = _require_run(run_id)
    run["spec"] = get_artifact_store().get_run_spec(run_id)
    return run


@router.get(
    "/{run_id}/spec",
    response_model=dict[str, Any],
    summary="Get submitted run spec",
)
def get_run_spec(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    spec = get_artifact_store().get_run_spec(run_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Run spec not found")
    return {"run_id": run_id, "spec": spec}


@router.get(
    "/{run_id}/result",
    response_model=dict[str, Any],
    summary="Get persisted final run result",
)
def get_run_result(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    result = get_artifact_store().get_run_result(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run result not available")
    return {"run_id": run_id, "result": result}


@router.get(
    "/{run_id}/rounds",
    response_model=dict[str, Any],
    summary="List recorded rounds and round snapshots for a run",
)
def list_run_rounds(
    run_id: str,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    snapshots = get_artifact_store().list_run_rounds(
        run_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return {
        "run_id": run_id,
        "available_rounds": [snapshot["round"] for snapshot in snapshots],
        "snapshots": snapshots,
        "count": len(snapshots),
    }


@router.get(
    "/{run_id}/rounds/{round_number}",
    response_model=dict[str, Any],
    summary="Get one recorded round snapshot",
)
def get_run_round(run_id: str, round_number: int) -> dict[str, Any]:
    _require_run(run_id)
    snapshot = get_artifact_store().get_run_round(run_id, round_number)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Round {round_number} not found")
    return {"run_id": run_id, "snapshot": snapshot}


@router.get(
    "/{run_id}/events",
    response_model=dict[str, Any],
    summary="Get persisted event history for a run",
)
def get_run_events(
    run_id: str,
    event_type: str | None = None,
    agent_id: str | None = None,
    round: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    events = get_artifact_store().get_run_events(run_id)
    return {
        "run_id": run_id,
        "events": filter_events(
            events,
            event_type=event_type,
            agent_id=agent_id,
            round_number=round,
            limit=limit,
            offset=offset,
        ),
    }


@router.get(
    "/{run_id}/agents/{agent_id}/timeline",
    response_model=dict[str, Any],
    summary="Get one agent's state across recorded rounds",
)
def get_agent_timeline(
    run_id: str,
    agent_id: str,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    rounds = get_artifact_store().list_run_rounds(run_id, start=start, end=end, limit=limit, offset=offset)
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "timeline": agent_timeline_from_rounds(rounds, agent_id),
    }


@router.get(
    "/{run_id}/snapshots",
    response_model=dict[str, Any],
    summary="List named snapshots stored for a run",
)
def list_named_snapshots(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    snapshots = get_artifact_store().list_named_snapshots(run_id=run_id)
    return {"run_id": run_id, "snapshots": snapshots}
