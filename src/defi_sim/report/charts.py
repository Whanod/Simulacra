"""Reusable Plotly chart builders for common simulation visualization."""

from __future__ import annotations


import pandas as pd
import plotly.graph_objects as go


def figure_to_plotly_dict(fig: go.Figure) -> dict[str, object]:
    """Return a Plotly figure as a JSON-safe dict."""
    return fig.to_plotly_json()


def figure_to_json(fig: go.Figure, *, pretty: bool = False) -> str:
    """Return a Plotly figure as a JSON string."""
    return fig.to_json(pretty=pretty)


def leaderboard(
    df: pd.DataFrame,
    group_col: str,
    score_col: str,
    title: str = "Leaderboard",
) -> go.Figure:
    """Ranked horizontal bar chart."""
    sorted_df = df.sort_values(score_col, ascending=True)
    fig = go.Figure(go.Bar(
        x=sorted_df[score_col],
        y=sorted_df[group_col].astype(str),
        orientation='h',
    ))
    fig.update_layout(title=title, xaxis_title=score_col, yaxis_title=group_col)
    return fig


def leaderboard_dict(
    df: pd.DataFrame,
    group_col: str,
    score_col: str,
    title: str = "Leaderboard",
) -> dict[str, object]:
    return figure_to_plotly_dict(leaderboard(df, group_col, score_col, title=title))


def leaderboard_json(
    df: pd.DataFrame,
    group_col: str,
    score_col: str,
    title: str = "Leaderboard",
    *,
    pretty: bool = False,
) -> str:
    return figure_to_json(leaderboard(df, group_col, score_col, title=title), pretty=pretty)


def box_plot(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
    title: str = "Metric Distribution",
) -> go.Figure:
    """Metric distribution by group."""
    fig = go.Figure()
    for group in df[group_col].unique():
        subset = df[df[group_col] == group]
        fig.add_trace(go.Box(y=subset[metric_col], name=str(group)))
    fig.update_layout(title=title, yaxis_title=metric_col)
    return fig


def box_plot_dict(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
    title: str = "Metric Distribution",
) -> dict[str, object]:
    return figure_to_plotly_dict(box_plot(df, group_col, metric_col, title=title))


def box_plot_json(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
    title: str = "Metric Distribution",
    *,
    pretty: bool = False,
) -> str:
    return figure_to_json(box_plot(df, group_col, metric_col, title=title), pretty=pretty)


def time_series_with_bands(
    series: list[float] | pd.Series,
    ci_low: list[float] | pd.Series | None = None,
    ci_high: list[float] | pd.Series | None = None,
    title: str = "Time Series",
    y_label: str = "Value",
) -> go.Figure:
    """Convergence curves with confidence intervals."""
    x = list(range(len(series)))
    fig = go.Figure()

    if ci_low is not None and ci_high is not None:
        fig.add_trace(go.Scatter(
            x=x + x[::-1],
            y=list(ci_high) + list(ci_low)[::-1],
            fill='toself',
            fillcolor='rgba(68, 68, 68, 0.2)',
            line=dict(color='rgba(255,255,255,0)'),
            name='CI',
        ))

    fig.add_trace(go.Scatter(x=x, y=list(series), mode='lines', name='Mean'))
    fig.update_layout(title=title, xaxis_title="Round", yaxis_title=y_label)
    return fig


def time_series_with_bands_dict(
    series: list[float] | pd.Series,
    ci_low: list[float] | pd.Series | None = None,
    ci_high: list[float] | pd.Series | None = None,
    title: str = "Time Series",
    y_label: str = "Value",
) -> dict[str, object]:
    return figure_to_plotly_dict(
        time_series_with_bands(series, ci_low=ci_low, ci_high=ci_high, title=title, y_label=y_label)
    )


def time_series_with_bands_json(
    series: list[float] | pd.Series,
    ci_low: list[float] | pd.Series | None = None,
    ci_high: list[float] | pd.Series | None = None,
    title: str = "Time Series",
    y_label: str = "Value",
    *,
    pretty: bool = False,
) -> str:
    return figure_to_json(
        time_series_with_bands(series, ci_low=ci_low, ci_high=ci_high, title=title, y_label=y_label),
        pretty=pretty,
    )


def heatmap(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    value_col: str,
    title: str = "Heatmap",
) -> go.Figure:
    """2D parameter grid visualization."""
    pivot = df.pivot_table(values=value_col, index=y_col, columns=x_col, aggfunc='mean')
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[str(c) for c in pivot.columns],
        y=[str(r) for r in pivot.index],
        colorscale='Viridis',
    ))
    fig.update_layout(title=title, xaxis_title=x_col, yaxis_title=y_col)
    return fig


def heatmap_dict(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    value_col: str,
    title: str = "Heatmap",
) -> dict[str, object]:
    return figure_to_plotly_dict(heatmap(df, x_col, y_col, value_col, title=title))


def heatmap_json(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    value_col: str,
    title: str = "Heatmap",
    *,
    pretty: bool = False,
) -> str:
    return figure_to_json(heatmap(df, x_col, y_col, value_col, title=title), pretty=pretty)
