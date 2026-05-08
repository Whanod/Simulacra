"""FastAPI application for defi-sim."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from defi_sim_api.routers import (
    calibration,
    charts,
    embed,
    engines,
    exports,
    jsonrpc_solana,
    metrics,
    orderbook,
    parameters,
    population,
    predicates,
    replay,
    reports,
    registry,
    share,
    runs,
    simulate_bundle,
    snapshots,
    simulations,
    sweeps,
    templates,
    validation,
    wallet_persistence,
)
from defi_sim_api.schemas import HealthResponse

CORS_ALLOWED_ORIGINS_ENV = "CORS_ALLOWED_ORIGINS"
_DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _parse_cors_origins(raw: str | None) -> list[str]:
    value = raw if raw is not None else _DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def create_app() -> FastAPI:
    """Build a fresh FastAPI app. Tests use this to exercise env-var overrides
    (in particular, CORS_ALLOWED_ORIGINS is read at app construction time)."""
    new_app = FastAPI(
        title="defi-sim API",
        description="Web API for the defi-sim DeFi simulation library",
        version="0.1.0",
    )

    new_app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(os.environ.get(CORS_ALLOWED_ORIGINS_ENV)),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    new_app.include_router(simulations.router)
    new_app.include_router(runs.router)
    new_app.include_router(snapshots.router)
    new_app.include_router(engines.router)
    new_app.include_router(parameters.router)
    new_app.include_router(registry.router)
    new_app.include_router(metrics.router)
    new_app.include_router(charts.router)
    new_app.include_router(embed.router)
    new_app.include_router(sweeps.router)
    new_app.include_router(validation.router)
    new_app.include_router(population.router)
    new_app.include_router(reports.router)
    new_app.include_router(exports.router)
    new_app.include_router(orderbook.router)
    new_app.include_router(predicates.router)
    new_app.include_router(templates.router)
    new_app.include_router(replay.router)
    new_app.include_router(simulate_bundle.router)
    new_app.include_router(share.router)
    new_app.include_router(wallet_persistence.router)
    new_app.include_router(jsonrpc_solana.router)
    new_app.include_router(calibration.router)

    @new_app.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse()

    return new_app


app = create_app()
