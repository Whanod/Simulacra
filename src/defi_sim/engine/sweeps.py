"""MC orchestration with parallel execution.

Generalized sweep framework for parameter grid exploration.
"""

from __future__ import annotations

import copy
import itertools
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from defi_sim.core.agent import Agent
from defi_sim.core.market import Market
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.simulation import SimulationEngine


@dataclass
class SweepConfig:
    """Defines a parameter grid for Monte Carlo sweep."""
    market_factory: Callable[..., Market]
    agent_factory: Callable[..., list[Agent]]
    param_grid: dict[str, list]
    num_runs: int = 10
    seeds: list[int] | None = None
    master_seed: int | None = None
    num_rounds: int = 200
    base_config: SimulationConfig | None = None
    executor: Executor | None = None
    metric_fn: Callable[..., dict[str, float]] | None = None


def _run_single(
    market_factory: Callable,
    agent_factory: Callable,
    params: dict[str, Any],
    seed: int,
    num_rounds: int,
    base_config: SimulationConfig | None,
    metric_fn: Callable | None,
) -> dict[str, Any]:
    """Run a single simulation with given params and seed."""
    market = market_factory(**params)
    agents = agent_factory(**params)

    config = SimulationConfig(
        num_rounds=num_rounds,
        seed=seed,
        **({"numeric_mode": base_config.numeric_mode} if base_config else {}),
    )
    if base_config:
        config.default_fee_model = base_config.default_fee_model
        config.clock = base_config.clock
        config.execution_model = copy.deepcopy(base_config.execution_model)

    engine = SimulationEngine(market=market, agents=agents, config=config)
    result = engine.run()

    row: dict[str, Any] = dict(params)
    row["seed"] = seed
    row["num_rounds_executed"] = result.num_rounds_executed
    row["stopped_early"] = result.stopped_early

    if metric_fn is not None:
        metrics = metric_fn(result)
        row.update(metrics)

    return row


def run_sweep(config: SweepConfig) -> pd.DataFrame:
    """Run all parameter combinations, return flat DataFrame."""
    param_names = list(config.param_grid.keys())
    param_values = list(config.param_grid.values())
    combinations = list(itertools.product(*param_values))
    combination_runs: list[tuple[dict[str, Any], list[int]]] = []
    if config.seeds is not None:
        for combo in combinations:
            params = dict(zip(param_names, combo))
            combination_runs.append((params, list(config.seeds)))
    else:
        master_seed = config.master_seed
        if master_seed is None and config.base_config is not None:
            master_seed = config.base_config.seed
        if master_seed is None:
            master_seed = 42
        master_rng = np.random.default_rng(master_seed)
        for combo in combinations:
            params = dict(zip(param_names, combo))
            seeds = [int(master_rng.integers(0, 2**31)) for _ in range(config.num_runs)]
            combination_runs.append((params, seeds))

    rows: list[dict[str, Any]] = []

    if config.executor is not None:
        # Parallel execution
        futures: list[Future] = []
        for params, seeds in combination_runs:
            for seed in seeds:
                f = config.executor.submit(
                    _run_single,
                    config.market_factory,
                    config.agent_factory,
                    params,
                    seed,
                    config.num_rounds,
                    config.base_config,
                    config.metric_fn,
                )
                futures.append(f)
        for f in futures:
            rows.append(f.result())
    else:
        # Sequential
        for params, seeds in combination_runs:
            for seed in seeds:
                row = _run_single(
                    config.market_factory,
                    config.agent_factory,
                    params,
                    seed,
                    config.num_rounds,
                    config.base_config,
                    config.metric_fn,
                )
                rows.append(row)

    return pd.DataFrame(rows)


def gate(
    df: pd.DataFrame,
    checks: dict[str, Callable[[pd.DataFrame], bool]],
) -> tuple[bool, dict[str, bool]]:
    """Run validity checks on sweep results."""
    results = {}
    for name, check in checks.items():
        results[name] = check(df)
    return all(results.values()), results


def rank(
    df: pd.DataFrame,
    metric_columns: list[str],
    weights: dict[str, float] | None = None,
    lower_is_better: dict[str, bool] | None = None,
    group_col: str | None = None,
    top_k: int = 3,
) -> pd.DataFrame:
    """Pareto-normalize metrics, compute composite scores, return top_k."""
    if lower_is_better is None:
        lower_is_better = {}
    if weights is None:
        weights = {m: 1.0 for m in metric_columns}

    # Group by param combo if needed
    if group_col:
        grouped = df.groupby(group_col)[metric_columns].mean().reset_index()
    else:
        # Average over seeds for each param combo
        non_metric = [c for c in df.columns if c not in metric_columns and c != "seed"]
        if non_metric:
            grouped = df.groupby(non_metric)[metric_columns].mean().reset_index()
        else:
            grouped = df.copy()

    # Normalize each metric to [0, 1]
    for col in metric_columns:
        vals = grouped[col]
        mn, mx = vals.min(), vals.max()
        if mx > mn:
            normalized = (vals - mn) / (mx - mn)
        else:
            normalized = pd.Series(0.5, index=vals.index)

        # Flip if lower is better
        if lower_is_better.get(col, True):
            normalized = 1.0 - normalized

        grouped[f"{col}_norm"] = normalized

    # Composite score
    grouped["composite_score"] = sum(
        grouped[f"{col}_norm"] * weights.get(col, 1.0)
        for col in metric_columns
    )

    return grouped.nlargest(top_k, "composite_score")


def sensitivity(df: pd.DataFrame, param: str, metric: str) -> pd.DataFrame:
    """Compute sensitivity of a metric to a parameter."""
    return df.groupby(param)[metric].agg(["mean", "std", "min", "max"]).reset_index()
