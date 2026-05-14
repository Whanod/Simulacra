"""Durable run retrieval, comparison, and snapshot catalog endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from defi_sim_api.backend.overview_aggregations import (
    aggregate_bundle_outcomes_summary,
    aggregate_jito_searcher_summary,
    aggregate_solana_slot_summary,
    latest_replay_metrics,
)
from defi_sim_api.backend.serialization import (
    agent_summary,
    agent_timeline_from_rounds,
    price_summary,
)
from defi_sim_api.backend.store import get_artifact_store

router = APIRouter(prefix="/runs", tags=["runs"])


def _require_run(run_id: str) -> dict[str, Any]:
    run = get_artifact_store().get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id!r} not found",
        )
    return run


def _flatten_diff(prefix: str, left: Any, right: Any, out: dict[str, dict[str, Any]]) -> None:
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        for key in sorted(keys):
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            _flatten_diff(next_prefix, left.get(key), right.get(key), out)
        return
    if left != right:
        out[prefix or "value"] = {"left": left, "right": right}


@router.post(
    "/compare",
    response_model=dict[str, Any],
    summary="Compare two durable runs",
)
def compare_runs(body: dict[str, str]) -> dict[str, Any]:
    left_run_id = body.get("left_run_id")
    right_run_id = body.get("right_run_id")
    if not left_run_id or not right_run_id:
        raise HTTPException(status_code=422, detail="left_run_id and right_run_id are required")

    store = get_artifact_store()
    left_run = _require_run(left_run_id)
    right_run = _require_run(right_run_id)
    left_spec = store.get_run_spec(left_run_id) or {}
    right_spec = store.get_run_spec(right_run_id) or {}
    # Phase 5.2: read from typed surfaces instead of the legacy
    # ``runs.result`` JSONB pluck. ``num_rounds`` / ``stopped_early`` /
    # ``cancelled`` come off ``runs.summary`` (populated by
    # :func:`summarize_result` at write time); ``num_rounds_executed``
    # rides on ``runs.current_round``; ``seed`` has its own column.
    # ``price_summary`` / ``agent_summary`` read the relevant typed
    # column directly via the legacy-shape adapter at the bottom of this
    # function. The previous ``metadata_diff`` field is dropped — no
    # frontend consumer reads it (see ``frontend/.../compare.ts``, the
    # type is marked optional and no page consumes it).
    left_summary = left_run.get("summary") or {}
    right_summary = right_run.get("summary") or {}
    left_slices = store.read_overview_typed_slices(left_run_id)
    right_slices = store.read_overview_typed_slices(right_run_id)

    spec_diff: dict[str, dict[str, Any]] = {}
    _flatten_diff("", left_spec, right_spec, spec_diff)

    metric_diff: dict[str, dict[str, Any]] = {}
    for key, left_value, right_value in (
        ("num_rounds", left_summary.get("num_rounds"), right_summary.get("num_rounds")),
        ("num_rounds_executed", left_run.get("current_round"), right_run.get("current_round")),
        ("seed", left_run.get("seed"), right_run.get("seed")),
        ("stopped_early", left_summary.get("stopped_early"), right_summary.get("stopped_early")),
        ("cancelled", left_summary.get("cancelled"), right_summary.get("cancelled")),
    ):
        metric_diff[key] = {
            "left": left_value,
            "right": right_value,
            "delta": (right_value - left_value)
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float))
            else None,
        }

    left_result_view = {
        "price_history": left_slices.get("price_history"),
        "agent_final_states": left_slices.get("agent_final_states"),
    }
    right_result_view = {
        "price_history": right_slices.get("price_history"),
        "agent_final_states": right_slices.get("agent_final_states"),
    }
    left_prices = price_summary(left_result_view)
    right_prices = price_summary(right_result_view)
    price_delta: dict[str, dict[str, Any]] = {}
    for key in sorted(set(left_prices) | set(right_prices)):
        left_entry = left_prices.get(key, {})
        right_entry = right_prices.get(key, {})
        left_end = left_entry.get("end")
        right_end = right_entry.get("end")
        price_delta[key] = {
            "left": left_entry,
            "right": right_entry,
            "delta_end": (right_end - left_end)
            if isinstance(left_end, (int, float)) and isinstance(right_end, (int, float))
            else None,
        }

    left_agents = agent_summary(left_result_view)
    right_agents = agent_summary(right_result_view)
    agent_delta: dict[str, dict[str, Any]] = {}
    for key in sorted(set(left_agents) | set(right_agents)):
        left_entry = left_agents.get(key, {})
        right_entry = right_agents.get(key, {})
        agent_delta[key] = {
            "left": left_entry,
            "right": right_entry,
            "delta_realized_pnl": (
                right_entry.get("realized_pnl") - left_entry.get("realized_pnl")
                if isinstance(left_entry.get("realized_pnl"), (int, float))
                and isinstance(right_entry.get("realized_pnl"), (int, float))
                else None
            ),
        }

    return {
        "left_run_id": left_run_id,
        "right_run_id": right_run_id,
        "equal": not spec_diff and all(item["delta"] in {0, None} for item in metric_diff.values()),
        "spec_diff": spec_diff,
        "metric_diff": metric_diff,
        "price_summary_delta": price_delta,
        "agent_summary_delta": agent_delta,
    }


@router.post(
    "/aggregate",
    response_model=dict[str, Any],
    summary="Sum one metric across many runs via the round_metrics table",
)
def aggregate_runs(body: dict[str, Any]) -> dict[str, Any]:
    # Sibling of /runs/compare (which is a pairwise diff the UI consumes).
    # This is the multi-run SQL aggregation the migration plan calls for —
    # exposed under a distinct path so the existing compare view keeps
    # working until Phase 4 frontend work consolidates the two.
    run_ids = body.get("run_ids")
    metric = body.get("metric")
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=422, detail="run_ids must be a non-empty list")
    if not isinstance(metric, str) or not metric:
        raise HTTPException(status_code=422, detail="metric is required")
    agent = body.get("agent_id")

    try:
        rows = get_artifact_store().aggregate_round_metrics(
            run_ids, metric, agent_id=agent
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "metric": metric,
        "agent_id": agent,
        "runs": rows,
    }


@router.get(
    "",
    response_model=dict[str, Any],
    summary="List durable runs",
)
def list_runs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    store = get_artifact_store()
    runs = store.list_runs(limit=limit, offset=offset)
    return {
        "runs": runs,
        "count": store.count_runs(),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{run_id}",
    response_model=dict[str, Any],
    summary="Get durable run metadata",
)
def get_run(run_id: str) -> dict[str, Any]:
    run = _require_run(run_id)
    run["spec"] = get_artifact_store().get_run_spec(run_id)
    return run


@router.get(
    "/{run_id}/spec",
    response_model=dict[str, Any],
    summary="Get submitted run spec",
)
def get_run_spec(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    spec = get_artifact_store().get_run_spec(run_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Run spec not found")
    return {"run_id": run_id, "spec": spec}


@router.get(
    "/{run_id}/rounds",
    response_model=dict[str, Any],
    summary="List recorded rounds and round snapshots for a run",
)
def list_run_rounds(
    run_id: str,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    snapshots = get_artifact_store().list_run_rounds(
        run_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return {
        "run_id": run_id,
        "available_rounds": [snapshot["round"] for snapshot in snapshots],
        "snapshots": snapshots,
        "count": len(snapshots),
    }


@router.get(
    "/{run_id}/rounds/{round_number}",
    response_model=dict[str, Any],
    summary="Get one recorded round snapshot",
)
def get_run_round(run_id: str, round_number: int) -> dict[str, Any]:
    _require_run(run_id)
    snapshot = get_artifact_store().get_run_round(run_id, round_number)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Round {round_number} not found")
    return {"run_id": run_id, "snapshot": snapshot}


@router.get(
    "/{run_id}/events",
    response_model=dict[str, Any],
    summary="Get persisted event history for a run",
)
def get_run_events(
    run_id: str,
    event_type: str | None = None,
    agent_id: str | None = None,
    round: int | None = None,
    from_round: int | None = Query(None, alias="from"),
    to_round: int | None = Query(None, alias="to"),
    cursor: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    events = get_artifact_store().query_run_events(
        run_id,
        event_type=event_type,
        agent_id=agent_id,
        round_number=round,
        from_round=from_round,
        to_round=to_round,
        cursor=cursor,
        limit=limit,
        offset=offset,
    )
    response: dict[str, Any] = {"run_id": run_id, "events": events}
    # Emit next_cursor only when there's likely more — keeps the response
    # byte-equal with the unbounded ``?limit=N`` callers (incl. goldens).
    if len(events) == limit and events:
        response["next_cursor"] = events[-1]["event_id"]
    return response


@router.get(
    "/{run_id}/correlations/{correlation_id}",
    response_model=dict[str, Any],
    summary="All events sharing one correlation_id, in event_id order",
)
def get_run_correlation(run_id: str, correlation_id: str) -> dict[str, Any]:
    _require_run(run_id)
    # Correlation chains are bounded (a swap or sandwich is at most a handful
    # of events); skip cursor pagination here and return the full set. The
    # underlying SQL uses the events_run_correlation partial index.
    events = get_artifact_store().query_run_events(
        run_id,
        correlation_id=correlation_id,
        limit=10_000,
    )
    return {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "events": events,
    }


@router.get(
    "/{run_id}/metrics/{metric}",
    response_model=dict[str, Any],
    summary="Per-round metric series from the pre-aggregated round_metrics table",
)
def get_run_metric(
    run_id: str,
    metric: str,
    agent: str | None = None,
    from_round: int | None = Query(None, alias="from"),
    to_round: int | None = Query(None, alias="to"),
) -> dict[str, Any]:
    _require_run(run_id)
    try:
        series = get_artifact_store().query_round_metrics(
            run_id,
            metric,
            agent_id=agent,
            from_round=from_round,
            to_round=to_round,
        )
    except ValueError as exc:
        # Unknown metric column; tell the caller which ones are valid.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "run_id": run_id,
        "metric": metric,
        "agent_id": agent,
        "from": from_round,
        "to": to_round,
        "series": series,
    }


@router.get(
    "/{run_id}/views/overview",
    response_model=dict[str, Any],
    summary="Page-shaped bundle for the run results dashboard",
)
def get_run_overview(run_id: str) -> dict[str, Any]:
    """Compose the one round-trip the results page needs.

    Tiles come from the ``runs.derived_metrics`` typed column (populated by
    Phase 5.1's dual-write from the engine's
    ``metadata.derived_metrics`` map), filtered to finite numerics. The
    same set ``RecommendedMetricsGrid`` consumes today. Templates' own
    ``recommended_metrics`` field lives in a separate namespace
    (``final_yes_price``, ``stopped_early`` etc.) and isn't resolved here;
    see the migration plan's API-surface section.

    Phase 5.2 retired the ``query_overview_result_slices`` JSONB-pluck
    against ``runs.result``: every slice now reads from its own typed
    column on ``runs`` or from the ``round_snapshots`` table. The
    user-visible response shape is unchanged except that the always-null
    ``volume_history`` / ``liquidity_history`` keys are dropped (the
    engine never populated them).

    Bundle is best-effort consistent across its slices: a still-running
    run can see ``event_summary`` advance beyond ``series``. Acceptable
    because the view targets terminal runs (results page); live runs use
    the simulations event stream instead.
    """
    run = _require_run(run_id)
    store = get_artifact_store()
    spec = store.get_run_spec(run_id) or {}
    # Inline spec on the run so the frontend gets the full SimRun.spec
    # without a second /runs/{id} fetch on initial paint — matches the
    # ``GET /runs/{id}`` handler's contract.
    run["spec"] = spec

    slices = store.read_overview_typed_slices(run_id)
    derived = slices.get("derived_metrics") or {}
    tiles: dict[str, float] = {}
    for key, value in derived.items():
        # Booleans subclass ``int`` — drop them explicitly so tiles stays a
        # true str→number map. Infinity preserved as a sentinel
        # (``fees_vs_il_breakeven``); NaN dropped — matches the frontend's
        # ``derivedNumericMetrics`` adapter.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and value != value:
            continue
        tiles[key] = value

    market = spec.get("market") if isinstance(spec.get("market"), dict) else {}
    agent_types: list[str] = []
    for agent in spec.get("agents") or []:
        if isinstance(agent, dict):
            agent_type = agent.get("type")
            if isinstance(agent_type, str):
                agent_types.append(agent_type)
    spec_summary = {
        "market_type": market.get("type"),
        "agent_types": agent_types,
        "num_rounds": spec.get("num_rounds"),
        "seed": spec.get("seed"),
    }

    series: dict[str, list[dict[str, Any]]] = {}
    for metric in ("volume", "num_actions", "num_failed", "gas_spent"):
        series[metric] = store.query_round_metrics(run_id, metric)

    event_summary = store.summarize_run_events(run_id)
    fee_history = store.query_fee_history(run_id)
    snapshot_slices = store.query_round_snapshot_summaries(run_id)
    snapshot_summaries = snapshot_slices.get("snapshot_summaries") or []

    # Phase 4 page rewire: aggregate the per-round Solana / bundle /
    # Jito-searcher / replay payloads on the server. The page used to do
    # this iteration over ``result.round_snapshots`` client-side; folding it
    # into the view bundle keeps initial paint to a single fetch.
    solana_slot_summary = aggregate_solana_slot_summary(snapshot_summaries)
    bundle_outcomes_summary = aggregate_bundle_outcomes_summary(snapshot_summaries)
    jito_searcher_summary = aggregate_jito_searcher_summary(snapshot_summaries)
    replay_metrics = latest_replay_metrics(snapshot_summaries)

    return {
        "run": run,
        "spec_summary": spec_summary,
        "tiles": tiles,
        "series": series,
        "event_summary": event_summary,
        "price_history": slices.get("price_history"),
        "agent_final_states": slices.get("agent_final_states"),
        "whirlpool_snapshots": snapshot_slices.get("whirlpool_snapshots"),
        "sandwich_summary": slices.get("sandwich_summary"),
        "replay_diff": slices.get("replay_diff"),
        "fee_history": fee_history,
        "num_rounds_executed": slices.get("current_round"),
        "solana_slot_summary": solana_slot_summary,
        "bundle_outcomes_summary": bundle_outcomes_summary,
        "jito_searcher_summary": jito_searcher_summary,
        "replay_metrics": replay_metrics,
    }


@router.get(
    "/{run_id}/agents/{agent_id}/timeline",
    response_model=dict[str, Any],
    summary="Get one agent's state across recorded rounds",
)
def get_agent_timeline(
    run_id: str,
    agent_id: str,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    _require_run(run_id)
    rounds = get_artifact_store().list_run_rounds(run_id, start=start, end=end, limit=limit, offset=offset)
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "timeline": agent_timeline_from_rounds(rounds, agent_id),
    }


@router.get(
    "/{run_id}/snapshots",
    response_model=dict[str, Any],
    summary="List named snapshots stored for a run",
)
def list_named_snapshots(run_id: str) -> dict[str, Any]:
    _require_run(run_id)
    snapshots = get_artifact_store().list_named_snapshots(run_id=run_id)
    return {"run_id": run_id, "snapshots": snapshots}
