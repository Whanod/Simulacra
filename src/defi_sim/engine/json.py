"""JSON serialization helpers for web-facing APIs."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Mapping

from defi_sim.core.types import SimulationResult

JS_SAFE_INTEGER: int = (1 << 53) - 1
BIGINT_MARKER: str = "__defi_sim_bigint__"
TYPE_MARKER: str = "__type__"
MAPPING_MARKER: str = "mapping"


def _is_dataclass_instance(value: Any) -> bool:
    return is_dataclass(value) and not isinstance(value, type)


def to_jsonable(value: Any, *, include_type_tags: bool = True) -> Any:
    """Convert supported engine objects into JSON-safe Python data."""
    if _is_dataclass_instance(value):
        payload: dict[str, Any] = {}
        if include_type_tags:
            payload[TYPE_MARKER] = value.__class__.__name__
        for field in fields(value):
            payload[field.name] = to_jsonable(
                getattr(value, field.name),
                include_type_tags=include_type_tags,
            )
        return payload

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {
                key: to_jsonable(inner, include_type_tags=include_type_tags)
                for key, inner in value.items()
            }
        return {
            TYPE_MARKER: MAPPING_MARKER,
            "entries": [
                {
                    "key": to_jsonable(key, include_type_tags=include_type_tags),
                    "value": to_jsonable(inner, include_type_tags=include_type_tags),
                }
                for key, inner in value.items()
            ],
        }

    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(inner, include_type_tags=include_type_tags) for inner in value]

    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, int):
        if abs(value) > JS_SAFE_INTEGER:
            return {BIGINT_MARKER: str(value)}
        return value

    if isinstance(value, (float, str)):
        return value

    raise TypeError(f"unsupported JSON serialization type: {type(value)!r}")


def decode_jsonable(value: Any) -> Any:
    """Reverse `to_jsonable` for JSON-native trees used by web specs."""
    if isinstance(value, list):
        return [decode_jsonable(inner) for inner in value]

    if isinstance(value, dict):
        if set(value.keys()) == {BIGINT_MARKER}:
            return int(value[BIGINT_MARKER])

        if value.get(TYPE_MARKER) == MAPPING_MARKER and "entries" in value:
            return {
                decode_jsonable(entry["key"]): decode_jsonable(entry["value"])
                for entry in value["entries"]
            }

        return {key: decode_jsonable(inner) for key, inner in value.items()}

    return value


def to_json(
    value: Any,
    *,
    indent: int | None = None,
    include_type_tags: bool = True,
) -> str:
    return json.dumps(
        to_jsonable(value, include_type_tags=include_type_tags),
        indent=indent,
        sort_keys=False,
    )


def simulation_result_to_dict(result: SimulationResult) -> dict[str, Any]:
    return to_jsonable(result, include_type_tags=True)


def simulation_result_to_json(
    result: SimulationResult,
    *,
    indent: int | None = None,
) -> str:
    return to_json(result, indent=indent, include_type_tags=True)
