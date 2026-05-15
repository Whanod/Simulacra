"""Helpers for persisting live and completed simulation artifacts."""

from __future__ import annotations

from typing import Any

from defi_sim.engine.events import Event, EventType

from defi_sim_api.backend.serialization import (
    events_to_list,
    market_type_from_spec,
    price_summary,
    agent_summary,
    result_to_dict,
    round_snapshot_to_dict,
    summarize_result,
)
from defi_sim_api.backend.store import get_artifact_store
from defi_sim_api.state import EngineEntry


def create_live_run_record(
    run_id: str,
    spec: dict[str, Any],
    *,
    owner_id: str | None = None,
) -> dict[str, Any]:
    store = get_artifact_store()
    return store.create_run(
        run_id,
        spec=spec,
        status="live",
        seed=spec.get("seed"),
        market_type=market_type_from_spec(spec),
        source="live",
        simulation_id=run_id,
        current_round=0,
        summary={
            "market_type": market_type_from_spec(spec),
            "seed": spec.get("seed"),
            "status": "live",
            "current_round": 0,
            "available_rounds": [],
            "agent_count": len(spec.get("agents", [])),
        },
        owner_id=owner_id,
    )


def persist_replay_run(
    run_id: str,
    *,
    slot_range: tuple[int, int] | None = None,
    counterfactuals: list[Any] | None = None,
    predicted: dict[str, Any] | None = None,
    replay_diff: dict[str, Any] | None = None,
    round_snapshots: list[dict[str, Any]] | None = None,
    status: str = "completed",
    seed: int | None = None,
    decoded_transaction_share: float | None = None,
    unsupported_program_ids: list[str] | None = None,
    replay_kind: str | None = None,
    mainnet_accuracy_claim: bool | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Persist a replay run as a first-class artifact (PRD line 331).

    Lives in the same artifact store as regular runs (Postgres-backed); the
    metadata flag ``kind == "replay"`` and the
    ``counterfactuals: list[CounterfactualSpec]`` field on the run summary
    distinguish it from sync/live runs. ``decoded_transaction_share`` and
    ``unsupported_program_ids`` are persisted on the summary per PRD line 361
    so callers reading the artifact (not just the POST response) can tell what
    fraction of the slot range was decoded and which program IDs blocked
    coverage.
    """
    from defi_sim.engine.replay_execution import Counterfactual

    cf_specs: list[dict[str, Any]] = []
    for cf in counterfactuals or []:
        if isinstance(cf, Counterfactual):
            cf_specs.append(cf.to_spec().to_dict())
        elif isinstance(cf, dict):
            cf_specs.append(cf)
        else:
            to_spec = getattr(cf, "to_spec", None)
            if callable(to_spec):
                spec = to_spec()
                cf_specs.append(spec.to_dict() if hasattr(spec, "to_dict") else dict(spec))
            else:
                cf_specs.append({"kind": type(cf).__name__, "params": {}})

    spec_payload: dict[str, Any] = {
        "kind": "replay",
        "slot_range": list(slot_range) if slot_range is not None else None,
        "counterfactuals": cf_specs,
    }
    summary: dict[str, Any] = {
        "kind": "replay",
        "counterfactuals": cf_specs,
        "slot_range": spec_payload["slot_range"],
        "status": status,
    }
    if replay_diff is not None:
        summary["replay_diff"] = replay_diff
    if predicted is not None:
        # Stash on summary so the Phase 5 composer (`pg_store.get_run_result`)
        # and the legacy `/runs/{id}/result` endpoint can surface the same
        # `predicted` payload the replay tests assert against. ``runs.result``
        # used to be the only home for this; once Phase 5.3 retires the
        # endpoint, the field becomes summary-only.
        summary["predicted"] = predicted
    if decoded_transaction_share is not None:
        summary["decoded_transaction_share"] = decoded_transaction_share
    if unsupported_program_ids is not None:
        summary["unsupported_program_ids"] = list(unsupported_program_ids)
    # PRD US-002 line 338: persist the replay-kind marker and mainnet-accuracy
    # claim flag so consumers reading the artifact (not just the POST response)
    # know whether to surface accuracy numbers.
    if replay_kind is not None:
        summary["replay_kind"] = replay_kind
    if mainnet_accuracy_claim is not None:
        summary["mainnet_accuracy_claim"] = mainnet_accuracy_claim

    store = get_artifact_store()
    store.create_run(
        run_id,
        spec=spec_payload,
        status=status,
        seed=seed,
        market_type=None,
        source="replay",
        simulation_id=run_id,
        current_round=0,
        summary=summary,
        owner_id=owner_id,
    )
    result_payload: dict[str, Any] = {
        "kind": "replay",
        "predicted": predicted or {},
        "round_snapshots": list(round_snapshots or []),
    }
    if replay_diff is not None:
        result_payload["replay_diff"] = replay_diff
    store.save_run_artifacts(
        run_id,
        result=result_payload,
        events=[],
        round_snapshots=list(round_snapshots or []),
        summary=summary,
    )
    return store.get_run(run_id) or {}


def persist_sync_run(
    run_id: str,
    *,
    spec: dict[str, Any],
    result: Any,
    events: list[Event],
    owner_id: str | None = None,
) -> dict[str, Any]:
    store = get_artifact_store()
    result_payload = result_to_dict(result)
    event_payloads = events_to_list(events)
    summary = summarize_result(
        spec=spec,
        result=result_payload,
        events=event_payloads,
        current_round=int(result_payload.get("num_rounds_executed", 0)),
        status="completed",
    )
    summary["price_summary"] = price_summary(result_payload)
    summary["agent_summary"] = agent_summary(result_payload)
    store.create_run(
        run_id,
        spec=spec,
        status="completed",
        seed=spec.get("seed"),
        market_type=market_type_from_spec(spec),
        source="sync",
        simulation_id=run_id,
        current_round=int(result_payload.get("num_rounds_executed", 0)),
        summary=summary,
        owner_id=owner_id,
    )
    store.save_run_artifacts(
        run_id,
        result=result_payload,
        events=event_payloads,
        round_snapshots=[round_snapshot_to_dict(snapshot) for snapshot in result.round_snapshots],
        summary=summary,
    )
    return store.get_run(run_id) or {}


def ensure_completion_event(entry: EngineEntry) -> None:
    if entry.completion_event_emitted:
        return
    if not entry.engine.is_complete:
        return
    # Phase 5 (postgres-migration plan, line 252): SIMULATION_END no longer
    # carries the full result. The engine writes the typed columns + peer
    # tables directly via ``persist_live_entry`` → ``save_run_artifacts``;
    # the event row is now just a "done" marker.
    entry.event_bus.emit(
        Event(
            type=EventType.SIMULATION_END,
            round=entry.engine.current_round,
            timestamp=entry.engine._clock.timestamp(entry.engine.current_round),  # noqa: SLF001
        )
    )
    entry.completion_event_emitted = True


def persist_live_entry(entry: EngineEntry, *, status: str | None = None) -> dict[str, Any]:
    store = get_artifact_store()
    current_status = status or ("completed" if entry.engine.is_complete else "live")
    if entry.engine.is_complete:
        ensure_completion_event(entry)

    events = events_to_list(entry.event_bus.history)
    result_payload = None
    round_payloads = [round_snapshot_to_dict(snapshot) for snapshot in entry.engine._snapshots]  # noqa: SLF001
    if entry.engine.is_complete:
        result_payload = result_to_dict(entry.engine._build_result())  # noqa: SLF001

    summary = summarize_result(
        spec=entry.spec,
        result=result_payload,
        events=events,
        current_round=entry.engine.current_round,
        status=current_status,
    )
    summary["price_summary"] = price_summary(result_payload)
    summary["agent_summary"] = agent_summary(result_payload)
    store.save_run_artifacts(
        entry.run_id,
        spec=entry.spec,
        result=result_payload,
        events=events,
        round_snapshots=round_payloads,
        summary=summary,
    )
    return store.update_run(
        entry.run_id,
        status=current_status,
        current_round=entry.engine.current_round,
        summary=summary,
    )
