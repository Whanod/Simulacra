"""Operator-only endpoints. Auth: shared admin token in env var
``DEFI_SIM_ADMIN_TOKEN``, sent via the ``X-Admin-Token`` header.

Fail-closed: if the env var is unset or empty, every admin endpoint returns
503. This prevents an unconfigured deploy from silently exposing destructive
operations. The token is compared in constant time with ``hmac.compare_digest``
to avoid timing oracles.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from defi_sim_api.backend.store import get_artifact_store

ADMIN_TOKEN_ENV = "DEFI_SIM_ADMIN_TOKEN"

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(token: str | None) -> None:
    expected = os.environ.get(ADMIN_TOKEN_ENV) or ""
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"admin endpoints disabled: {ADMIN_TOKEN_ENV} not set",
        )
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin token",
        )


@router.post(
    "/purge",
    response_model=dict[str, Any],
    summary="Delete every run and its artifacts from the store",
)
def purge(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_admin_token)
    deleted = get_artifact_store().purge_runs()
    return {"status": "ok", "deleted": deleted}
