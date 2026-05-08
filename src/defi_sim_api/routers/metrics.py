"""Metric computation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.json import to_jsonable
from defi_sim.engine.world import World
from defi_sim.metrics.generic import (
    FeesVsILBreakeven,
    LPInRangeFraction,
    MaxDrawdown,
    RangeIL,
    RollingVolatility,
    TWAP,
    convergence_speed,
    convergence_speed_revised,
    kl_divergence,
    lp_profitability,
    manipulation_cost,
    manipulation_resistance_revised,
)
from defi_sim.metrics.registry import MetricRegistry

from defi_sim_api import schemas, state

router = APIRouter(prefix="/metrics", tags=["metrics"])

_BATCH_METRICS: dict[str, object] = {
    "kl_divergence": kl_divergence,
    "convergence_speed": convergence_speed,
    "convergence_speed_revised": convergence_speed_revised,
    "lp_profitability": lp_profitability,
    "manipulation_cost": manipulation_cost,
    "manipulation_resistance_revised": manipulation_resistance_revised,
}

_STREAMING_METRIC_TYPES: dict[str, type] = {
    "max_drawdown": MaxDrawdown,
    "rolling_volatility": RollingVolatility,
    "twap": TWAP,
    "lp_in_range_fraction": LPInRangeFraction,
    "range_il": RangeIL,
    "fees_vs_il_breakeven": FeesVsILBreakeven,
}


@router.get(
    "",
    response_model=dict[str, list[str]],
    summary="List available built-in metric functions (batch and streaming)",
)
def list_metrics() -> dict[str, list[str]]:
    return {
        "batch": sorted(_BATCH_METRICS.keys()),
        "streaming": sorted(_STREAMING_METRIC_TYPES.keys()),
    }


@router.post(
    "/compute",
    response_model=schemas.MetricsResponse,
    summary="Compute named metrics on provided data",
)
def compute_metrics(body: schemas.MetricsComputeRequest) -> schemas.MetricsResponse:
    values: dict[str, float] = {}
    for name, spec in body.metrics.items():
        fn = _BATCH_METRICS.get(spec.get("type", name))
        if fn is None:
            continue
        params = dict(spec.get("params", {}))
        try:
            values[name] = float(fn(**params))
        except Exception:
            values[name] = float("nan")
    return schemas.MetricsResponse(metrics=values)


# ── Streaming metrics on live engines ─────────────────────────────────────

_engine_registries: dict[str, MetricRegistry] = {}


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


@router.post(
    "/streaming/{simulation_id}/register",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
    summary="Register streaming metrics on a live engine",
)
def register_streaming_metrics(
    simulation_id: str,
    body: dict[str, dict[str, object]],
) -> dict[str, object]:
    entry = _get_entry(simulation_id)

    if simulation_id not in _engine_registries:
        registry = MetricRegistry()
        _engine_registries[simulation_id] = registry
    else:
        registry = _engine_registries[simulation_id]

    registered = []
    for name, spec in body.items():
        metric_type = spec.get("type", name)
        cls = _STREAMING_METRIC_TYPES.get(metric_type)
        if cls is None:
            continue
        params = dict(spec.get("params", {}))
        metric = cls(**params)
        # Range-aware metrics need a handle to the live market so they
        # can read concentrated-LP positions each round.
        bind_fn = getattr(metric, "bind_market", None)
        if callable(bind_fn):
            engine = getattr(entry, "engine", None)
            market = getattr(engine, "_market", None) if engine is not None else None
            if market is not None:
                bind_fn(market)
        lower = bool(spec.get("lower_is_better", True))
        weight = float(spec.get("weight", 0.0))
        registry.register_streaming(name, metric, lower_is_better=lower, weight=weight)
        registered.append(name)

    registry.subscribe_to(entry.event_bus)
    return {"registered": registered}


@router.get(
    "/streaming/{simulation_id}",
    response_model=schemas.MetricsResponse,
    summary="Finalize and return streaming metric values from a live engine",
)
def finalize_streaming_metrics(simulation_id: str) -> schemas.MetricsResponse:
    _get_entry(simulation_id)  # verify engine exists
    registry = _engine_registries.get(simulation_id)
    if registry is None:
        return schemas.MetricsResponse(metrics={})

    # Finalize streaming metrics by calling compute_all with a dummy result
    from defi_sim.core.types import SimulationResult
    values = registry.compute_all(SimulationResult())
    return schemas.MetricsResponse(metrics=values)
