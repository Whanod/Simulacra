"""Chart generation endpoints — return Plotly JSON for frontend rendering."""

from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter

from defi_sim.report.charts import (
    box_plot,
    heatmap,
    leaderboard,
    time_series_with_bands,
)

from defi_sim_api.schemas import (
    BoxPlotRequest,
    ChartResponse,
    HeatmapRequest,
    LeaderboardRequest,
    TimeSeriesRequest,
)

router = APIRouter(prefix="/charts", tags=["charts"])


def _fig_to_dict(fig) -> dict:
    """Convert Plotly figure to a JSON-safe dict (no numpy arrays)."""
    return json.loads(fig.to_json())


@router.post(
    "/leaderboard",
    response_model=ChartResponse,
    summary="Generate a leaderboard bar chart",
)
def leaderboard_chart(body: LeaderboardRequest) -> ChartResponse:
    df = pd.DataFrame(body.data)
    fig = leaderboard(df, body.group_col, body.score_col, title=body.title)
    return ChartResponse(chart=_fig_to_dict(fig))


@router.post(
    "/box-plot",
    response_model=ChartResponse,
    summary="Generate a box plot of metric distributions",
)
def box_plot_chart(body: BoxPlotRequest) -> ChartResponse:
    df = pd.DataFrame(body.data)
    fig = box_plot(df, body.group_col, body.metric_col, title=body.title)
    return ChartResponse(chart=_fig_to_dict(fig))


@router.post(
    "/time-series",
    response_model=ChartResponse,
    summary="Generate a time series chart with optional confidence bands",
)
def time_series_chart(body: TimeSeriesRequest) -> ChartResponse:
    fig = time_series_with_bands(
        body.series,
        ci_low=body.ci_low,
        ci_high=body.ci_high,
        title=body.title,
        y_label=body.y_label,
    )
    return ChartResponse(chart=_fig_to_dict(fig))


@router.post(
    "/heatmap",
    response_model=ChartResponse,
    summary="Generate a 2D heatmap from parameter grid data",
)
def heatmap_chart(body: HeatmapRequest) -> ChartResponse:
    df = pd.DataFrame(body.data)
    fig = heatmap(df, body.x_col, body.y_col, body.value_col, title=body.title)
    return ChartResponse(chart=_fig_to_dict(fig))
