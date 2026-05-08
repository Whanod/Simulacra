"""Self-contained embeddable chart widgets for durable run artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import html
import math
import os
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from defi_sim_api.backend.store import get_artifact_store
from defi_sim_api.routers.share import _resolve_share

EMBED_FRAME_ANCESTORS_ENV = "DEFI_SIM_EMBED_FRAME_ANCESTORS"
DEFAULT_EMBED_FRAME_ANCESTORS = "'self' http://localhost:* http://127.0.0.1:*"

router = APIRouter(tags=["embed"])

_BIGINT_MARKER = "__defi_sim_bigint__"
_PALETTE = ["#6c8aff", "#34d399", "#fbbf24", "#a78bfa", "#22d3ee", "#f472b6"]

_REPLAY_METRIC_LABELS: dict[str, str] = {
    "bundle-landing-rate": "Bundle landing rate",
    "tip-efficiency": "Tip efficiency",
    "slot-inclusion-latency": "Slot inclusion latency",
    "cu-per-dollar-tip-breakeven": "CU/$ tip break-even",
    "skip-rate-cost": "Skip-rate cost",
    "write-lock-heatmap": "Write-lock contention",
    "submission-path-comparison": "Submission path comparison",
}


@dataclass(frozen=True)
class EmbedSeries:
    label: str
    values: list[float]
    color: str


@dataclass(frozen=True)
class EmbedChart:
    chart_id: str
    title: str
    subtitle: str
    unit: str
    series: list[EmbedSeries]
    value_label: str | None = None


def _frame_ancestors() -> str:
    return os.environ.get(EMBED_FRAME_ANCESTORS_ENV, DEFAULT_EMBED_FRAME_ANCESTORS).strip()


def _normalize_chart_id(chart_id: str) -> str:
    normalized = chart_id.strip().lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
    return re.sub(r"-+", "-", normalized).strip("-")


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _to_chart_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else 0.0
    if _is_plain_object(value):
        encoded = value.get(_BIGINT_MARKER)
        if isinstance(encoded, str):
            try:
                number = float(encoded)
            except ValueError:
                return 0.0
            return number if math.isfinite(number) else 0.0
    return 0.0


def _cumulative(values: list[float]) -> list[float]:
    out: list[float] = []
    total = 0.0
    for value in values:
        total += value
        out.append(total)
    return out


def _sum_fee_split_value(value: Any) -> float:
    if _is_plain_object(value):
        if isinstance(value.get(_BIGINT_MARKER), str):
            return _to_chart_number(value)
        return sum(_to_chart_number(inner) for inner in value.values())
    return _to_chart_number(value)


def _total_fees_per_round(fee_history: Any) -> list[float]:
    if not isinstance(fee_history, list):
        return []
    values: list[float] = []
    for splits in fee_history:
        if not _is_plain_object(splits):
            values.append(0.0)
            continue
        values.append(sum(_sum_fee_split_value(value) for value in splits.values()))
    return values


def _fees_by_destination(fee_history: Any) -> list[EmbedSeries]:
    if not isinstance(fee_history, list):
        return []
    destinations: set[str] = set()
    for splits in fee_history:
        if _is_plain_object(splits):
            destinations.update(str(key) for key in splits)
    series: list[EmbedSeries] = []
    for index, destination in enumerate(sorted(destinations)):
        running = 0.0
        values: list[float] = []
        for splits in fee_history:
            value = splits.get(destination) if _is_plain_object(splits) else None
            running += _sum_fee_split_value(value)
            values.append(running)
        series.append(
            EmbedSeries(
                label=destination,
                values=values,
                color=_PALETTE[index % len(_PALETTE)],
            )
        )
    return sorted(series, key=lambda item: item.values[-1] if item.values else 0.0, reverse=True)


def _price_series(price_history: Any) -> list[EmbedSeries]:
    if not isinstance(price_history, list) or not price_history:
        return []
    first = price_history[0]
    if not _is_plain_object(first):
        return []
    series: list[EmbedSeries] = []
    for index, key in enumerate(first.keys()):
        values = [
            _to_chart_number(point.get(key)) if _is_plain_object(point) else 0.0
            for point in price_history
        ]
        series.append(
            EmbedSeries(label=str(key), values=values, color=_PALETTE[index % len(_PALETTE)])
        )
    return series


def _liquidity_from_rounds(round_snapshots: Any) -> list[float]:
    if not isinstance(round_snapshots, list):
        return []
    values: list[float] = []
    for snapshot in round_snapshots:
        if not _is_plain_object(snapshot):
            continue
        market_state = snapshot.get("market_state")
        if _is_plain_object(market_state):
            total_liquidity = market_state.get("total_liquidity")
            if isinstance(total_liquidity, (int, float, dict)):
                values.append(_to_chart_number(total_liquidity))
                continue
        all_states = snapshot.get("all_market_states")
        if not _is_plain_object(all_states):
            continue
        total = 0.0
        found = False
        for state in all_states.values():
            if not _is_plain_object(state):
                continue
            total_liquidity = state.get("total_liquidity")
            if isinstance(total_liquidity, (int, float, dict)):
                total += _to_chart_number(total_liquidity)
                found = True
        if found:
            values.append(total)
    return values


def _cumulative_volume_from_rounds(round_snapshots: Any) -> list[float]:
    if not isinstance(round_snapshots, list):
        return []
    values: list[float] = []
    for snapshot in round_snapshots:
        if not _is_plain_object(snapshot):
            continue
        agent_states = snapshot.get("agent_states")
        if not _is_plain_object(agent_states):
            continue
        total = 0.0
        for state in agent_states.values():
            if _is_plain_object(state):
                total += _to_chart_number(state.get("cumulative_volume"))
        values.append(total)
    return values


def _latest_replay_metrics(result: dict[str, Any]) -> dict[str, Any]:
    snapshots = result.get("round_snapshots")
    if not isinstance(snapshots, list):
        return {}
    for snapshot in reversed(snapshots):
        if not _is_plain_object(snapshot):
            continue
        metrics = snapshot.get("metrics")
        replay = metrics.get("replay") if _is_plain_object(metrics) else None
        if _is_plain_object(replay):
            return replay
    return {}


def _series_from_replay_metric(metric_key: str, metric: dict[str, Any]) -> list[EmbedSeries]:
    if metric_key == "slot-inclusion-latency" and isinstance(metric.get("samples"), list):
        return [
            EmbedSeries(
                label="Latency samples",
                values=[_to_chart_number(value) for value in metric["samples"]],
                color=_PALETTE[0],
            )
        ]
    if metric_key == "cu-per-dollar-tip-breakeven":
        tips = metric.get("tips")
        ev = metric.get("extracted_values")
        series: list[EmbedSeries] = []
        if isinstance(tips, list):
            series.append(
                EmbedSeries(
                    label="Tips paid",
                    values=[_to_chart_number(value) for value in tips],
                    color=_PALETTE[0],
                )
            )
        if isinstance(ev, list):
            series.append(
                EmbedSeries(
                    label="Extracted value",
                    values=[_to_chart_number(value) for value in ev],
                    color=_PALETTE[1],
                )
            )
        return series
    if metric_key == "write-lock-heatmap" and isinstance(metric.get("counts"), list):
        values = []
        for item in metric["counts"]:
            if _is_plain_object(item):
                values.append(_to_chart_number(item.get("count")))
        return [EmbedSeries(label="Write locks", values=values, color=_PALETTE[0])]
    if metric_key == "submission-path-comparison" and isinstance(metric.get("landing_rates"), list):
        paths = metric.get("paths")
        labels = paths if isinstance(paths, list) else []
        return [
            EmbedSeries(
                label="Landing rate",
                values=[_to_chart_number(value) for value in metric["landing_rates"]],
                color=_PALETTE[0],
            ),
            EmbedSeries(
                label=", ".join(str(label) for label in labels[:3]) if labels else "Paths",
                values=[],
                color=_PALETTE[1],
            ),
        ]
    return [
        EmbedSeries(
            label=_REPLAY_METRIC_LABELS[metric_key],
            values=[_to_chart_number(metric.get("value"))],
            color=_PALETTE[0],
        )
    ]


def _build_chart(chart_id: str, result: dict[str, Any]) -> EmbedChart:
    normalized = _normalize_chart_id(chart_id)
    if normalized == "price-series":
        return EmbedChart(
            chart_id=normalized,
            title="Price Series",
            subtitle="Pool price history",
            unit="price",
            series=_price_series(result.get("price_history")),
        )
    if normalized == "cumulative-volume":
        raw_volume = result.get("volume_history")
        volume = _cumulative([_to_chart_number(value) for value in raw_volume]) if isinstance(raw_volume, list) else []
        if not volume:
            volume = _cumulative_volume_from_rounds(result.get("round_snapshots"))
        return EmbedChart(
            chart_id=normalized,
            title="Cumulative Volume",
            subtitle="Market-wide traded volume",
            unit="volume",
            series=[EmbedSeries(label="Cumulative volume", values=volume, color=_PALETTE[0])],
        )
    if normalized == "liquidity-over-time":
        raw_liquidity = result.get("liquidity_history")
        liquidity = (
            [_to_chart_number(value) for value in raw_liquidity]
            if isinstance(raw_liquidity, list)
            else []
        )
        if not liquidity:
            liquidity = _liquidity_from_rounds(result.get("round_snapshots"))
        return EmbedChart(
            chart_id=normalized,
            title="Liquidity Over Time",
            subtitle="Total modeled liquidity",
            unit="liquidity",
            series=[EmbedSeries(label="Total liquidity", values=liquidity, color=_PALETTE[3])],
        )
    if normalized == "cumulative-fees":
        fees = _cumulative(_total_fees_per_round(result.get("fee_history")))
        return EmbedChart(
            chart_id=normalized,
            title="Cumulative Fees",
            subtitle="Fees collected across the run",
            unit="fees",
            series=[EmbedSeries(label="Cumulative fees", values=fees, color=_PALETTE[1])],
        )
    if normalized == "fees-by-destination":
        return EmbedChart(
            chart_id=normalized,
            title="Fees by Destination",
            subtitle="Cumulative fee split",
            unit="fees",
            series=_fees_by_destination(result.get("fee_history")),
        )
    if normalized in _REPLAY_METRIC_LABELS:
        replay_metrics = _latest_replay_metrics(result)
        raw_metric = replay_metrics.get(normalized.replace("-", "_"))
        if not _is_plain_object(raw_metric):
            raw_metric = replay_metrics.get(normalized)
        if not _is_plain_object(raw_metric):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Replay metric {chart_id!r} not found for run",
            )
        unit = str(raw_metric.get("unit") or "value")
        value = _to_chart_number(raw_metric.get("value"))
        return EmbedChart(
            chart_id=normalized,
            title=_REPLAY_METRIC_LABELS[normalized],
            subtitle=f"{int(_to_chart_number(raw_metric.get('sample_size')))} samples",
            unit=unit,
            series=_series_from_replay_metric(normalized, raw_metric),
            value_label=f"{value:g} {unit}",
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Embed chart {chart_id!r} is not supported",
    )


def _format_number(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _render_svg(chart: EmbedChart) -> str:
    width = 720
    height = 340
    pad_left = 72
    pad_top = 34
    pad_right = 28
    pad_bottom = 46
    chart_width = width - pad_left - pad_right
    chart_height = height - pad_top - pad_bottom

    values = [value for series in chart.series for value in series.values if math.isfinite(value)]
    if not values:
        return (
            f'<svg role="img" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            '<rect width="100%" height="100%" rx="12" fill="#181c26"/>'
            '<text x="50%" y="50%" fill="#6b7a94" font-family="-apple-system, Segoe UI, sans-serif" '
            'font-size="16" text-anchor="middle">No chart data available</text>'
            "</svg>"
        )

    min_value = min(values)
    max_value = max(values)
    if min_value > 0:
        min_value = 0.0
    if min_value == max_value:
        min_value -= 1.0
        max_value += 1.0
    value_range = max_value - min_value
    max_len = max((len(series.values) for series in chart.series), default=1)

    def x_at(index: int) -> float:
        denom = max(max_len - 1, 1)
        return pad_left + (index / denom) * chart_width

    def y_at(value: float) -> float:
        return pad_top + (1 - (value - min_value) / value_range) * chart_height

    parts: list[str] = [
        f'<svg role="img" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" rx="12" fill="#181c26"/>',
    ]
    for index in range(5):
        y = pad_top + (chart_height / 4) * index
        label = _format_number(max_value - (value_range / 4) * index)
        parts.append(f'<line x1="{pad_left}" y1="{y:.2f}" x2="{width - pad_right}" y2="{y:.2f}" stroke="#2a3040"/>')
        parts.append(
            f'<text x="{pad_left - 10}" y="{y + 4:.2f}" fill="#6b7a94" '
            f'font-family="ui-monospace, SFMono-Regular, monospace" font-size="11" text-anchor="end">{html.escape(label)}</text>'
        )
    parts.append(
        f'<line x1="{pad_left}" y1="{height - pad_bottom}" x2="{width - pad_right}" y2="{height - pad_bottom}" stroke="#3d4a66"/>'
    )
    parts.append(
        f'<line x1="{pad_left}" y1="{pad_top}" x2="{pad_left}" y2="{height - pad_bottom}" stroke="#3d4a66"/>'
    )

    legend_x = pad_left
    for series in chart.series:
        if not series.values:
            continue
        path = " ".join(
            f"{'M' if index == 0 else 'L'} {x_at(index):.2f} {y_at(value):.2f}"
            for index, value in enumerate(series.values)
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{html.escape(series.color)}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        if len(series.values) == 1:
            parts.append(
                f'<circle cx="{x_at(0):.2f}" cy="{y_at(series.values[0]):.2f}" r="4" fill="{html.escape(series.color)}"/>'
            )
        label = html.escape(series.label)
        parts.append(f'<rect x="{legend_x}" y="12" width="18" height="3" fill="{html.escape(series.color)}"/>')
        parts.append(
            f'<text x="{legend_x + 24}" y="17" fill="#a8b2c4" font-family="-apple-system, Segoe UI, sans-serif" font-size="11">{label}</text>'
        )
        legend_x += 24 + min(len(label) * 7, 170) + 18

    parts.append("</svg>")
    return "".join(parts)


def _render_html(chart: EmbedChart, *, run_id: str, open_path: str) -> str:
    title = html.escape(chart.title)
    subtitle = html.escape(chart.subtitle)
    run = html.escape(run_id)
    chart_id = html.escape(chart.chart_id)
    value = html.escape(chart.value_label or "")
    open_href = html.escape(open_path, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} embed</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #0a0c10; color: #e8ecf4; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ min-height: 100vh; padding: 16px; display: flex; flex-direction: column; gap: 12px; }}
    header {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }}
    h1 {{ margin: 2px 0 0; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0; color: #a8b2c4; font-size: 12px; }}
    .eyebrow {{ color: #6b7a94; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ color: #34d399; font-family: ui-monospace, SFMono-Regular, monospace; white-space: nowrap; }}
    .chart {{ border: 1px solid #2a3040; border-radius: 12px; overflow: hidden; background: #181c26; }}
    .chart svg {{ display: block; width: 100%; height: auto; }}
    footer {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
    a {{ color: #8aa2ff; text-decoration: none; font-size: 12px; font-weight: 600; }}
  </style>
</head>
<body>
  <main data-run-id="{run}" data-chart-id="{chart_id}">
    <header>
      <div>
        <p class="eyebrow">defi-sim embed</p>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <p class="value">{value}</p>
    </header>
    <div class="chart">{_render_svg(chart)}</div>
    <footer>
      <p>Run <span>{run}</span></p>
      <a href="{open_href}" target="_blank" rel="noopener noreferrer">Open run</a>
    </footer>
  </main>
</body>
</html>"""


@router.get(
    "/embed/{chart_id}",
    response_class=HTMLResponse,
    summary="Render a self-contained embeddable chart for a durable run",
)
def get_embed_chart(
    chart_id: str,
    run: str = Query(..., min_length=1, description="Run ID to render"),
) -> HTMLResponse:
    share = _resolve_share(run)
    result = get_artifact_store().get_run_result(run)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run result {run!r} not found",
        )
    chart = _build_chart(chart_id, result)
    response = HTMLResponse(_render_html(chart, run_id=run, open_path=share["page_path"]))
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "img-src data:; "
        "style-src 'unsafe-inline'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        f"frame-ancestors {_frame_ancestors()}"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
