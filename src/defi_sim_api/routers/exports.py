"""Data export endpoints — return CSV, JSON, or Parquet as file downloads."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse

from defi_sim_api.schemas import ExportRequest

router = APIRouter(prefix="/export", tags=["export"])


@router.post(
    "/csv",
    summary="Export data as CSV download",
    response_class=Response,
)
def export_csv(body: ExportRequest) -> Response:
    df = pd.DataFrame(body.data)
    if body.fields:
        df = df[body.fields]
    content = df.to_csv(index=False)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=export.csv"},
    )


@router.post(
    "/json",
    summary="Export data as JSON download",
    response_class=Response,
)
def export_json(body: ExportRequest) -> Response:
    df = pd.DataFrame(body.data)
    if body.fields:
        df = df[body.fields]
    content = df.to_json(orient="records", indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=export.json"},
    )


@router.post(
    "/parquet",
    summary="Export data as Parquet download",
    response_class=Response,
)
def export_parquet(body: ExportRequest) -> Response:
    df = pd.DataFrame(body.data)
    if body.fields:
        df = df[body.fields]
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=export.parquet"},
    )
