"""Sweep analysis endpoints (rank, sensitivity, run, gate)."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, HTTPException

from defi_sim.engine.sweeps import gate, rank, sensitivity

from defi_sim_api import state
from defi_sim_api.backend.store import get_artifact_store
from defi_sim_api.backend.sweeps import aggregate_rows, execute_sweep, gate_rows, recommend_rows
from defi_sim_api.schemas import (
    SweepAnalysisResponse,
    SweepGateRequest,
    SweepGateResponse,
    SweepRankRequest,
    SweepRunRequest,
    SweepSensitivityRequest,
)

router = APIRouter(prefix="/sweeps", tags=["sweeps"])


@router.post(
    "/rank",
    response_model=SweepAnalysisResponse,
    summary="Rank parameter combinations by composite score",
)
def rank_combinations(body: SweepRankRequest) -> SweepAnalysisResponse:
    df = pd.DataFrame(body.data)
    result = rank(
        df,
        body.metric_columns,
        weights=body.weights,
        lower_is_better=body.lower_is_better,
        group_col=body.group_col,
        top_k=body.top_k,
    )
    return SweepAnalysisResponse(data=result.to_dict(orient="records"))


@router.post(
    "/sensitivity",
    response_model=SweepAnalysisResponse,
    summary="Compute sensitivity of a metric to a parameter",
)
def compute_sensitivity(body: SweepSensitivityRequest) -> SweepAnalysisResponse:
    df = pd.DataFrame(body.data)
    result = sensitivity(df, body.param, body.metric)
    return SweepAnalysisResponse(data=result.to_dict(orient="records"))


@router.post(
    "/run",
    response_model=dict[str, object],
    summary="Run a durable parameter sweep across seed and patch combinations",
)
def run_sweep(body: SweepRunRequest) -> dict[str, object]:
    sweep_id = state.new_id()
    request_payload = body.model_dump(exclude_none=True)
    rows, summary = execute_sweep(
        spec=body.spec,
        param_grid=body.param_grid,
        num_runs=body.num_runs,
        seeds=body.seeds,
        master_seed=body.master_seed,
        metrics=body.metrics,
    )
    store = get_artifact_store()
    store.create_sweep(sweep_id, spec=request_payload, status="completed", summary=summary)
    store.save_sweep_artifacts(sweep_id, rows=rows, summary=summary)
    return {"sweep_id": sweep_id, "data": rows, "summary": summary}


@router.post(
    "/gate",
    response_model=SweepGateResponse,
    summary="Run validity gate checks on sweep result data",
)
def gate_check(body: SweepGateRequest) -> SweepGateResponse:
    passed, results = gate_rows(body.data, body.checks)
    return SweepGateResponse(passed=passed, results=results)


@router.get(
    "",
    response_model=dict[str, object],
    summary="List persisted sweeps",
)
def list_sweeps(limit: int = 100, offset: int = 0) -> dict[str, object]:
    store = get_artifact_store()
    sweeps = store.list_sweeps(limit=limit, offset=offset)
    return {
        "sweeps": sweeps,
        "count": store.count_sweeps(),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{sweep_id}",
    response_model=dict[str, object],
    summary="Get persisted sweep metadata",
)
def get_sweep(sweep_id: str) -> dict[str, object]:
    store = get_artifact_store()
    sweep = store.get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id!r} not found")
    sweep["spec"] = store.get_sweep_spec(sweep_id)
    return sweep


@router.get(
    "/{sweep_id}/rows",
    response_model=dict[str, object],
    summary="Get persisted sweep rows",
)
def get_sweep_rows(sweep_id: str) -> dict[str, object]:
    sweep = get_artifact_store().get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id!r} not found")
    rows = get_artifact_store().get_sweep_rows(sweep_id)
    return {"sweep_id": sweep_id, "data": rows}


@router.post(
    "/{sweep_id}/aggregates",
    response_model=SweepAnalysisResponse,
    summary="Aggregate durable sweep results by parameter combination",
)
def get_sweep_aggregates(sweep_id: str, body: dict[str, object]) -> SweepAnalysisResponse:
    sweep = get_artifact_store().get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id!r} not found")
    metric_columns = [str(item) for item in body.get("metric_columns", [])]
    group_by = body.get("group_by")
    rows = aggregate_rows(
        get_artifact_store().get_sweep_rows(sweep_id),
        metric_columns=metric_columns,
        group_by=[str(item) for item in group_by] if isinstance(group_by, list) else None,
    )
    return SweepAnalysisResponse(data=rows)


@router.post(
    "/{sweep_id}/recommendations",
    response_model=dict[str, object],
    summary="Rank and recommend sweep configurations against objectives and gates",
)
def get_sweep_recommendations(sweep_id: str, body: dict[str, object]) -> dict[str, object]:
    sweep = get_artifact_store().get_sweep(sweep_id)
    if sweep is None:
        raise HTTPException(status_code=404, detail=f"Sweep {sweep_id!r} not found")
    rows = get_artifact_store().get_sweep_rows(sweep_id)
    return recommend_rows(
        rows,
        objective_metrics=[str(item) for item in body.get("objective_metrics", [])],
        weights={str(k): float(v) for k, v in dict(body.get("weights", {})).items()},
        lower_is_better={str(k): bool(v) for k, v in dict(body.get("lower_is_better", {})).items()},
        gate_conditions=dict(body.get("gate_conditions", {})),
        top_k=int(body.get("top_k", 3)),
    )
