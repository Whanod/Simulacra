"""Experiment template catalog endpoints."""

from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter

from defi_sim_api.backend.lighthouse_sizing import apply_lighthouse_sizing
from defi_sim_api.backend.templates import experiment_templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get(
    "/experiments",
    response_model=dict[str, object],
    summary="List guided experiment templates",
)
def list_experiment_templates() -> dict[str, object]:
    # Resolve role-based agent sizing (lighthouse template) before handing
    # the catalog to the Builder, so opening a template shows agent trade
    # sizes already calibrated to the default ``initial_liquidity`` rather
    # than the unscaled placeholder integers stored in templates.py.
    templates = []
    for tpl in experiment_templates():
        tpl_out = deepcopy(tpl)
        if isinstance(tpl_out.get("base_spec"), dict):
            tpl_out["base_spec"] = apply_lighthouse_sizing(tpl_out["base_spec"])
        templates.append(tpl_out)
    return {"templates": templates, "count": len(templates)}
