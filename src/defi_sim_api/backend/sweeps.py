"""Sweep execution and analysis helpers."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pandas as pd

from defi_sim.engine.api import run_simulation
from defi_sim.engine.sweeps import gate, rank, sensitivity

from defi_sim_api.backend.patches import apply_spec_patches
from defi_sim_api.backend.serialization import result_to_dict


def _lookup_path(payload: Any, path: str) -> Any:
    cursor = payload
    for part in path.split("."):
        if isinstance(cursor, dict):
            cursor = cursor.get(part)
        else:
            return None
    return cursor


def extract_metric_values(result_payload: dict[str, Any], metrics: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    if not metrics:
        return {}

    extracted: dict[str, Any] = {}
    for name, spec in metrics.items():
        metric_type = spec.get("type", "field")
        if metric_type in {"field", "result_field"}:
            extracted[name] = _lookup_path(result_payload, spec.get("path", name))
            continue
        if metric_type == "final_price":
            token = spec.get("token")
            price_history = result_payload.get("price_history", [])
            extracted[name] = price_history[-1].get(token) if price_history and token is not None else None
            continue
        if metric_type == "price_delta":
            token = spec.get("token")
            price_history = result_payload.get("price_history", [])
            if len(price_history) >= 2 and token is not None:
                start = price_history[0].get(token)
                end = price_history[-1].get(token)
                extracted[name] = (end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
            else:
                extracted[name] = None
            continue
        if metric_type == "agent_balance":
            agent_id = str(spec.get("agent_id"))
            token = spec.get("token")
            state = result_payload.get("agent_final_states", {}).get(agent_id, {})
            extracted[name] = state.get("balances", {}).get(token) if token is not None else None
            continue
        if metric_type == "agent_realized_pnl":
            agent_id = str(spec.get("agent_id"))
            state = result_payload.get("agent_final_states", {}).get(agent_id, {})
            extracted[name] = state.get("realized_pnl")
            continue
        extracted[name] = None
    return extracted


def execute_sweep(
    *,
    spec: dict[str, Any],
    param_grid: dict[str, list[Any]],
    num_runs: int,
    seeds: list[int] | None,
    master_seed: int | None,
    metrics: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    param_names = list(param_grid.keys())
    combinations = list(itertools.product(*(param_grid[name] for name in param_names)))

    if seeds is not None:
        all_seeds = list(seeds)
    else:
        rng = np.random.default_rng(master_seed or spec.get("seed") or 42)
        all_seeds = [int(rng.integers(0, 2**31)) for _ in range(num_runs)]

    rows: list[dict[str, Any]] = []
    for combo in combinations:
        patches = dict(zip(param_names, combo))
        patched_spec = apply_spec_patches(spec, patches)
        for seed in all_seeds:
            run_spec = dict(patched_spec)
            run_spec["seed"] = seed
            row: dict[str, Any] = dict(patches)
            row["seed"] = seed
            try:
                result = run_simulation(run_spec)
                result_payload = result_to_dict(result)
                row["status"] = "completed"
                row["num_rounds_executed"] = result_payload.get("num_rounds_executed")
                row["stopped_early"] = result_payload.get("stopped_early")
                row["cancelled"] = result_payload.get("cancelled")
                row.update(extract_metric_values(result_payload, metrics))
            except Exception as exc:  # pragma: no cover - defensive path
                row["status"] = "failed"
                row["error"] = str(exc)
                row["num_rounds_executed"] = 0
                row["stopped_early"] = True
                row["cancelled"] = False
                if metrics:
                    for metric_name in metrics:
                        row[metric_name] = None
            rows.append(row)

    summary = {
        "row_count": len(rows),
        "metric_names": sorted(metrics.keys()) if metrics else [],
        "param_names": param_names,
        "seeds": all_seeds,
    }
    return rows, summary


def aggregate_rows(
    rows: list[dict[str, Any]],
    *,
    metric_columns: list[str],
    group_by: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    if group_by is None:
        excluded = set(metric_columns) | {"seed", "status", "error", "stopped_early", "cancelled", "num_rounds_executed"}
        group_by = [column for column in df.columns if column not in excluded]
    if not group_by:
        group_by = ["status"]

    grouped = df.groupby(group_by, dropna=False)
    output: list[dict[str, Any]] = []
    for keys, group in grouped:
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {column: value for column, value in zip(group_by, keys)}
        for metric in metric_columns:
            if metric not in group:
                continue
            series = pd.to_numeric(group[metric], errors="coerce")
            row[f"{metric}_mean"] = float(series.mean()) if not series.dropna().empty else None
            row[f"{metric}_std"] = float(series.std(ddof=0)) if len(series.dropna()) > 0 else None
            row[f"{metric}_min"] = float(series.min()) if not series.dropna().empty else None
            row[f"{metric}_max"] = float(series.max()) if not series.dropna().empty else None
        row["run_count"] = int(len(group))
        row["failure_rate"] = float((group["status"] != "completed").mean()) if "status" in group else 0.0
        if "stopped_early" in group:
            row["invalid_rate"] = float(group["stopped_early"].fillna(False).astype(bool).mean())
        else:
            row["invalid_rate"] = 0.0
        output.append(row)
    return output


def recommend_rows(
    rows: list[dict[str, Any]],
    *,
    objective_metrics: list[str],
    weights: dict[str, float] | None = None,
    lower_is_better: dict[str, bool] | None = None,
    gate_conditions: dict[str, dict[str, Any]] | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    if not rows:
        return {"top_configurations": [], "rejected_configurations": [], "next_experiment": None}

    df = pd.DataFrame(rows)
    if objective_metrics:
        excluded = set(objective_metrics) | {"seed", "status", "error", "stopped_early", "cancelled", "num_rounds_executed"}
        group_by = [column for column in df.columns if column not in excluded]
    else:
        group_by = [column for column in df.columns if column not in {"seed", "status", "error"}]
    grouped = df.groupby(group_by, dropna=False)[objective_metrics].mean().reset_index() if group_by else df.copy()

    rejected = pd.DataFrame(columns=grouped.columns)
    if gate_conditions:
        checks = {}
        for name, spec in gate_conditions.items():
            column = spec.get("column")
            op = spec.get("op", ">")
            threshold = spec.get("threshold", 0)
            if column is None:
                continue
            if op == ">":
                checks[name] = lambda d, c=column, t=threshold: d[c] > t
            elif op == ">=":
                checks[name] = lambda d, c=column, t=threshold: d[c] >= t
            elif op == "<":
                checks[name] = lambda d, c=column, t=threshold: d[c] < t
            elif op == "<=":
                checks[name] = lambda d, c=column, t=threshold: d[c] <= t
            elif op == "==":
                checks[name] = lambda d, c=column, t=threshold: d[c] == t
        if checks:
            mask = pd.Series(True, index=grouped.index)
            for check in checks.values():
                mask &= check(grouped).fillna(False)
            rejected = grouped[~mask]
            grouped = grouped[mask]

    ranked = rank(
        grouped,
        objective_metrics,
        weights=weights,
        lower_is_better=lower_is_better,
        top_k=top_k,
    ) if not grouped.empty and objective_metrics else grouped.head(top_k)

    next_experiment = None
    if objective_metrics and group_by:
        variability: list[tuple[str, float]] = []
        for parameter in group_by:
            if parameter in objective_metrics:
                continue
            scores: list[float] = []
            for metric in objective_metrics:
                frame = sensitivity(df[[parameter, metric]].dropna(), parameter, metric)
                if "std" in frame:
                    scores.append(float(frame["std"].fillna(0).mean()))
            variability.append((parameter, sum(scores)))
        if variability:
            variability.sort(key=lambda item: item[1], reverse=True)
            next_experiment = {
                "focus_parameter": variability[0][0],
                "rationale": "highest observed sensitivity gap across objective metrics",
            }

    return {
        "top_configurations": ranked.to_dict(orient="records"),
        "rejected_configurations": rejected.to_dict(orient="records"),
        "next_experiment": next_experiment,
    }


def gate_rows(rows: list[dict[str, Any]], checks: dict[str, dict[str, Any]]) -> tuple[bool, dict[str, bool]]:
    df = pd.DataFrame(rows)
    check_fns = {}
    for name, spec in checks.items():
        col = spec.get("column")
        op = spec.get("op", ">")
        threshold = spec.get("threshold", 0)
        if col is None:
            continue
        if op == ">":
            check_fns[name] = lambda d, c=col, t=threshold: bool((d[c] > t).all())
        elif op == ">=":
            check_fns[name] = lambda d, c=col, t=threshold: bool((d[c] >= t).all())
        elif op == "<":
            check_fns[name] = lambda d, c=col, t=threshold: bool((d[c] < t).all())
        elif op == "<=":
            check_fns[name] = lambda d, c=col, t=threshold: bool((d[c] <= t).all())
        elif op == "==":
            check_fns[name] = lambda d, c=col, t=threshold: bool((d[c] == t).all())
        elif op == "mean_>":
            check_fns[name] = lambda d, c=col, t=threshold: bool(d[c].mean() > t)
        elif op == "mean_<":
            check_fns[name] = lambda d, c=col, t=threshold: bool(d[c].mean() < t)
    return gate(df, check_fns)
