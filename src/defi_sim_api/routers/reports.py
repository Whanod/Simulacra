"""Durable report manifest and bundle export endpoints."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from defi_sim_api import state
from defi_sim_api.backend.store import get_artifact_store

router = APIRouter(prefix="/reports", tags=["reports"])


def _normalize_manifest(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": body.get("title", "Simulation Report"),
        "description": body.get("description"),
        "run_ids": list(body.get("run_ids", [])),
        "sweep_ids": list(body.get("sweep_ids", [])),
        "charts": list(body.get("charts", [])),
        "exports": list(body.get("exports", [])),
        "raw_artifacts": list(body.get("raw_artifacts", ["spec", "result", "events", "rows"])),
    }


def _build_bundle(report_id: str, manifest: dict[str, Any]) -> bytes:
    store = get_artifact_store()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

        include = set(manifest.get("raw_artifacts", []))
        for run_id in manifest.get("run_ids", []):
            run = store.get_run(run_id)
            if run is None:
                continue
            archive.writestr(f"runs/{run_id}/metadata.json", json.dumps(run, indent=2))
            if "spec" in include:
                spec = store.get_run_spec(run_id)
                if spec is not None:
                    archive.writestr(f"runs/{run_id}/spec.json", json.dumps(spec, indent=2))
            if "result" in include:
                result = store.get_run_result(run_id)
                if result is not None:
                    archive.writestr(f"runs/{run_id}/result.json", json.dumps(result, indent=2))
            if "events" in include:
                events = store.get_run_events(run_id)
                archive.writestr(f"runs/{run_id}/events.json", json.dumps(events, indent=2))
            if "rounds" in include:
                rounds = store.list_run_rounds(run_id, limit=10_000, offset=0)
                archive.writestr(f"runs/{run_id}/rounds.json", json.dumps(rounds, indent=2))

        for sweep_id in manifest.get("sweep_ids", []):
            sweep = store.get_sweep(sweep_id)
            if sweep is None:
                continue
            archive.writestr(f"sweeps/{sweep_id}/metadata.json", json.dumps(sweep, indent=2))
            rows = store.get_sweep_rows(sweep_id)
            if "rows" in include:
                archive.writestr(f"sweeps/{sweep_id}/rows.json", json.dumps(rows, indent=2))

        archive.writestr("charts.json", json.dumps(manifest.get("charts", []), indent=2))
        archive.writestr("exports.json", json.dumps(manifest.get("exports", []), indent=2))
        archive.writestr("report.json", json.dumps({"report_id": report_id}, indent=2))
    return buffer.getvalue()


_UPDATABLE_MANIFEST_FIELDS = {
    "title",
    "description",
    "run_ids",
    "sweep_ids",
    "charts",
    "exports",
    "raw_artifacts",
    "sections",
}
_UPDATABLE_REPORT_STATUSES = {"draft", "published", "ready"}


@router.post(
    "",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
    summary="Create a durable report manifest",
)
def create_report(body: dict[str, Any]) -> dict[str, object]:
    manifest = _normalize_manifest(body)
    if "sections" in body:
        manifest["sections"] = list(body["sections"])
    report_id = state.new_id()
    store = get_artifact_store()
    store.create_report(report_id, manifest=manifest, status="draft")
    return {"report_id": report_id, "manifest": manifest}


@router.get(
    "",
    response_model=dict[str, object],
    summary="List persisted reports",
)
def list_reports(limit: int = 100, offset: int = 0) -> dict[str, object]:
    store = get_artifact_store()
    reports = store.list_reports(limit=limit, offset=offset)
    return {
        "reports": reports,
        "count": store.count_reports(),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{report_id}",
    response_model=dict[str, object],
    summary="Fetch a durable report manifest",
)
def get_report(report_id: str) -> dict[str, object]:
    store = get_artifact_store()
    report = store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")
    manifest = store.get_report_manifest(report_id)
    return {"report": report, "manifest": manifest}


@router.put(
    "/{report_id}",
    response_model=dict[str, object],
    summary="Update a durable report (manifest fields and/or status)",
)
def update_report(report_id: str, body: dict[str, Any]) -> dict[str, object]:
    store = get_artifact_store()
    if store.get_report(report_id) is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")

    manifest_patch = {
        key: body[key] for key in body.keys() if key in _UPDATABLE_MANIFEST_FIELDS
    }
    if manifest_patch:
        updated = store.update_report_manifest(report_id, manifest_patch)
        if updated is None:
            raise HTTPException(
                status_code=404, detail=f"Report {report_id!r} manifest missing"
            )

    new_status = body.get("status")
    if new_status is not None:
        if new_status not in _UPDATABLE_REPORT_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"status must be one of {sorted(_UPDATABLE_REPORT_STATUSES)}",
            )
        store.update_report(report_id, status=new_status)

    report = store.get_report(report_id)
    manifest = store.get_report_manifest(report_id)
    return {"report": report, "manifest": manifest}


@router.delete(
    "/{report_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a durable report and its bundle",
)
def delete_report(report_id: str) -> Response:
    store = get_artifact_store()
    deleted = store.delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{report_id}/bundle",
    summary="Download a report bundle containing the manifest and selected artifacts",
    response_class=Response,
)
def export_report_bundle(report_id: str) -> Response:
    store = get_artifact_store()
    report = store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id!r} not found")
    manifest = store.get_report_manifest(report_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Report manifest not found")

    bundle_path = store.get_report_bundle_path(report_id)
    bundle_bytes: bytes
    if bundle_path is None:
        bundle_bytes = _build_bundle(report_id, manifest)
        store.save_report_bundle(report_id, bundle_bytes)
    else:
        bundle_bytes = Path(bundle_path).read_bytes()

    return Response(
        content=bundle_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=report-{report_id}.zip"},
    )
