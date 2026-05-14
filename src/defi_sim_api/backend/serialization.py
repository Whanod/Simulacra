"""Shared JSON-safe serializers and query helpers for backend artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from defi_sim.core.types import RoundSnapshot
from defi_sim.engine.events import Event
from defi_sim.engine.json import simulation_result_to_dict, to_jsonable


def market_type_from_spec(spec: dict[str, Any]) -> str | None:
    market = spec.get("market")
    if isinstance(market, dict):
        market_type = market.get("type")
        return str(market_type) if market_type is not None else None
    return None


def event_to_dict(event: Event) -> dict[str, Any]:
    event_type = event.type.name if hasattr(event.type, "name") else str(event.type)
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "type": event_type,
        "round": event.round,
        "timestamp": event.timestamp,
        "data": to_jsonable(event.data, include_type_tags=False),
    }


def events_to_list(events: Iterable[Event]) -> list[dict[str, Any]]:
    return [event_to_dict(event) for event in events]


def round_snapshot_to_dict(snapshot: RoundSnapshot) -> dict[str, Any]:
    return to_jsonable(snapshot, include_type_tags=True)


def result_to_dict(result: Any) -> dict[str, Any]:
    return simulation_result_to_dict(result)


def summarize_result(
    *,
    spec: dict[str, Any],
    result: dict[str, Any] | None,
    events: Sequence[dict[str, Any]],
    current_round: int,
    status: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "market_type": market_type_from_spec(spec),
        "seed": spec.get("seed"),
        "status": status,
        "current_round": current_round,
        "event_count": len(events),
        "available_rounds": [],
        "agent_count": len(spec.get("agents", [])),
    }
    if result is None:
        return summary

    round_snapshots = result.get("round_snapshots", [])
    summary["available_rounds"] = [snapshot["round"] for snapshot in round_snapshots]
    summary["num_rounds"] = result.get("num_rounds")
    summary["num_rounds_executed"] = result.get("num_rounds_executed")
    summary["stopped_early"] = result.get("stopped_early")
    summary["cancelled"] = result.get("cancelled")
    summary["stop_reason"] = result.get("stop_reason")
    summary["price_points"] = len(result.get("price_history", []))
    summary["final_agent_ids"] = sorted(str(agent_id) for agent_id in result.get("agent_final_states", {}).keys())
    return summary


def filter_events(
    events: Sequence[dict[str, Any]],
    *,
    event_type: str | None = None,
    agent_id: str | None = None,
    round_number: int | None = None,
    from_round: int | None = None,
    to_round: int | None = None,
    correlation_id: str | None = None,
    cursor: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filtered = list(events)
    if event_type is not None:
        filtered = [event for event in filtered if event.get("type") == event_type]
    if agent_id is not None:
        filtered = [event for event in filtered if str(event.get("data", {}).get("agent_id")) == str(agent_id)]
    if round_number is not None:
        filtered = [event for event in filtered if int(event.get("round", -1)) == round_number]
    if from_round is not None:
        filtered = [event for event in filtered if int(event.get("round", -1)) >= from_round]
    if to_round is not None:
        filtered = [event for event in filtered if int(event.get("round", -1)) <= to_round]
    if correlation_id is not None:
        filtered = [
            event for event in filtered
            if str(event.get("data", {}).get("correlation_id")) == str(correlation_id)
        ]
    if cursor is not None:
        # Cursor pagination: skip everything up to and including the event_id
        # the caller last consumed. Stable across concurrent inserts in a way
        # offset isn't — the events table grows append-only by event_id.
        filtered = [event for event in filtered if int(event.get("event_id", -1)) > cursor]
    return filtered[offset : offset + limit]


def agent_timeline_from_rounds(
    round_snapshots: Sequence[dict[str, Any]],
    agent_id: str,
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for snapshot in round_snapshots:
        agent_states = snapshot.get("agent_states", {})
        if agent_id not in agent_states:
            for key, value in agent_states.items():
                if str(key) == agent_id:
                    timeline.append(
                        {
                            "round": snapshot.get("round"),
                            "timestamp": snapshot.get("timestamp"),
                            "epoch": snapshot.get("epoch"),
                            "state": value,
                        }
                    )
                    break
            continue
        timeline.append(
            {
                "round": snapshot.get("round"),
                "timestamp": snapshot.get("timestamp"),
                "epoch": snapshot.get("epoch"),
                "state": agent_states[agent_id],
            }
        )
    return timeline


def price_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {}
    price_history = result.get("price_history", [])
    if not price_history:
        return {}
    first = price_history[0]
    last = price_history[-1]
    summary: dict[str, Any] = {}
    for key, end_value in last.items():
        start_value = first.get(key)
        if isinstance(start_value, (int, float)) and isinstance(end_value, (int, float)):
            summary[key] = {
                "start": start_value,
                "end": end_value,
                "delta": end_value - start_value,
            }
    return summary


def agent_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {}
    summary: dict[str, Any] = {}
    for agent_id, state in result.get("agent_final_states", {}).items():
        balances = state.get("balances", {})
        balance_total = 0.0
        for value in balances.values():
            if isinstance(value, (int, float)):
                balance_total += float(value)
        summary[str(agent_id)] = {
            "cumulative_volume": state.get("cumulative_volume"),
            "cumulative_volume_quote": state.get("cumulative_volume_quote"),
            "realized_pnl": state.get("realized_pnl"),
            "unrealized_pnl": state.get("unrealized_pnl"),
            "balance_total": balance_total,
        }
    return summary
