"""Shareable run-link endpoints for hosted/replay artifacts."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from defi_sim_api.backend.store import get_artifact_store
from defi_sim_solana.replay.corpus import corpus_root

PUBLIC_FRONTEND_URL_ENV = "DEFI_SIM_PUBLIC_FRONTEND_URL"
SHARE_LINK_TTL_DAYS = 30

router = APIRouter(tags=["share"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _encoded_run_id(run_id: str) -> str:
    return quote(run_id, safe="")


def _frontend_path(run_id: str, *, short: bool) -> str:
    prefix = "/r" if short else "/results"
    return f"{prefix}/{_encoded_run_id(run_id)}"


def _public_url(path: str) -> str:
    base = os.environ.get(PUBLIC_FRONTEND_URL_ENV, "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def _corpus_slots() -> set[int]:
    root = corpus_root()
    if not root.is_dir():
        return set()
    slots: set[int] = set()
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            slots.add(int(entry.name))
        except ValueError:
            continue
    return slots


def _slot_range_is_corpus_backed(slot_range: Any) -> bool:
    if not (
        isinstance(slot_range, (list, tuple))
        and len(slot_range) == 2
        and all(isinstance(slot, int) for slot in slot_range)
    ):
        return False
    start, end = slot_range
    if end < start:
        return False
    corpus_slots = _corpus_slots()
    if not corpus_slots:
        return False
    return all(slot in corpus_slots for slot in range(start, end + 1))


def _is_permanent(record: dict[str, Any]) -> bool:
    summary = record.get("summary") or {}
    if summary.get("permanent") is True or summary.get("saved") is True:
        return True
    if summary.get("persistence") == "permanent":
        return True
    if summary.get("kind") == "replay" and _slot_range_is_corpus_backed(
        summary.get("slot_range")
    ):
        return True
    return False


def _expires_at(record: dict[str, Any]) -> datetime | None:
    if _is_permanent(record):
        return None
    summary = record.get("summary") or {}
    explicit = _parse_timestamp(summary.get("expires_at"))
    if explicit is not None:
        return explicit
    created = _parse_timestamp(record.get("created_at")) or _utc_now()
    return created + timedelta(days=SHARE_LINK_TTL_DAYS)


def _resolve_share(run_id: str, *, include_artifacts: bool = False) -> dict[str, Any]:
    store = get_artifact_store()
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id!r} not found",
        )

    expires_at = _expires_at(record)
    if expires_at is not None and expires_at <= _utc_now():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Run link {run_id!r} has expired",
        )

    short_path = _frontend_path(run_id, short=True)
    results_path = _frontend_path(run_id, short=False)
    payload = {
        "run_id": run_id,
        "status": record.get("status"),
        "source": record.get("source"),
        "permanent": expires_at is None,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "page_path": short_path,
        "page_url": _public_url(short_path),
        "results_path": results_path,
        "results_url": _public_url(results_path),
        "run": record,
    }
    if include_artifacts:
        spec = store.get_run_spec(run_id)
        result = store.get_run_result(run_id)
        payload["spec"] = spec
        payload["result"] = result
        payload["run"] = {**record, "spec": spec}
    return payload


@router.get(
    "/share/runs/{run_id}",
    response_model=dict[str, Any],
    summary="Resolve a shareable run link to a durable run artifact",
)
def resolve_run_link(run_id: str) -> dict[str, Any]:
    return _resolve_share(run_id, include_artifacts=True)


@router.get(
    "/r/{run_id}",
    include_in_schema=False,
    summary="Short run link that redirects to the frontend results route",
)
def redirect_run_link(run_id: str) -> RedirectResponse:
    share = _resolve_share(run_id)
    return RedirectResponse(share["results_url"], status_code=status.HTTP_303_SEE_OTHER)
