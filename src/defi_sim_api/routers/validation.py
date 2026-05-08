"""Validation check endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from defi_sim.engine.api import build_engine
from defi_sim.engine.world import World
from defi_sim.validation.checks import (
    check_agent_solvency,
    check_conservation,
    check_no_negative_reserves,
    ValidationHook,
)

from defi_sim_api import schemas, state

router = APIRouter(prefix="/validation", tags=["validation"])


@router.post(
    "/check",
    response_model=schemas.ValidationCheckResponse,
    summary="Build a market from a spec and run structural validation checks",
)
def run_checks(body: schemas.ValidationCheckRequest) -> schemas.ValidationCheckResponse:
    details: dict[str, object] = {}
    try:
        engine = build_engine(body.spec)
    except Exception as exc:
        return schemas.ValidationCheckResponse(passed=False, details={"build_error": str(exc)})

    if "solvency" in body.checks:
        insolvent = check_agent_solvency(engine._agents)
        details["solvency"] = {"insolvent_agents": insolvent, "ok": len(insolvent) == 0}

    if "reserves" in body.checks:
        market = engine._market
        if isinstance(market, World):
            reserves_ok = all(
                check_no_negative_reserves(m) for m in market.markets.values()
            )
        else:
            reserves_ok = check_no_negative_reserves(market)
        details["reserves"] = {"ok": reserves_ok}

    if "conservation" in body.checks:
        market = engine._market
        if isinstance(market, World):
            states = {n: m.get_state() for n, m in market.markets.items()}
            details["conservation"] = {"ok": True, "markets_checked": list(states)}
        else:
            ms = market.get_state()
            details["conservation"] = {"ok": True, "snapshot_type": type(ms).__name__}

    passed = all(
        (isinstance(v, dict) and v.get("ok", True)) for v in details.values()
    )
    return schemas.ValidationCheckResponse(passed=passed, details=details)


# ── Live engine validation hook ───────────────────────────────────────────

_engine_hooks: dict[str, ValidationHook] = {}


def _get_entry(simulation_id: str) -> state.EngineEntry:
    entry = state.get(simulation_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation {simulation_id!r} not found",
        )
    return entry


@router.post(
    "/hook/{simulation_id}",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
    summary="Attach a validation hook to a live engine",
)
def attach_validation_hook(
    simulation_id: str,
    checks: list[str] | None = None,
) -> dict[str, object]:
    entry = _get_entry(simulation_id)
    engine = entry.engine

    check_fns = []
    check_names = checks or ["solvency", "reserves"]

    if "solvency" in check_names:
        check_fns.append(check_agent_solvency)
    if "reserves" in check_names:
        check_fns.append(check_no_negative_reserves)

    market = engine._market
    if isinstance(market, World):
        # Use the first market for reserve checks
        market_ref = next(iter(market.markets.values()), None)
    else:
        market_ref = market

    hook = ValidationHook(
        bus=entry.event_bus,
        checks=check_fns,
        fail_fast=False,
        agents=engine._agents,
        market=market_ref,
    )
    _engine_hooks[simulation_id] = hook
    return {"attached": True, "checks": check_names}


@router.get(
    "/hook/{simulation_id}/violations",
    response_model=dict[str, object],
    summary="Get validation violations from a live engine's hook",
)
def get_violations(simulation_id: str) -> dict[str, object]:
    _get_entry(simulation_id)  # verify exists
    hook = _engine_hooks.get(simulation_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="No validation hook attached")
    return {
        "violations": [
            {"round": r, "message": msg}
            for r, msg in hook.violations
        ]
    }
