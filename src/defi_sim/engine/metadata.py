"""Helpers for deriving registry metadata from dataclasses (BE-004).

The registry endpoint (BE-002) ships a ``schema`` and ``defaults`` payload
per entity so the frontend can render editors automatically. For most
builtins those come straight from the per-role params dataclass
(``NoiseParams``, ``ArbitrageParams``, …). This module centralizes the
derivation so every call site uses the same path, keeping the schemas
in sync with the engine.
"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from typing import Any

from pydantic import TypeAdapter


def schema_for_dataclass(cls: type) -> dict[str, Any]:
    """Return a JSON Schema for ``cls`` using pydantic's ``TypeAdapter``.

    Pydantic produces an object schema with ``properties`` derived from
    the dataclass fields, including each field's default, type, and
    title. This schema drops into ``EntityMetadata.schema`` unchanged.
    """
    if not is_dataclass(cls):
        raise TypeError(f"schema_for_dataclass expected a dataclass, got {cls!r}")
    adapter = TypeAdapter(cls)
    return adapter.json_schema()


def defaults_for_dataclass(cls: type) -> dict[str, Any]:
    """Return a ``{field_name: default_value}`` mapping for ``cls``.

    Reads each field's declared default (or ``default_factory`` result)
    directly off the dataclass — without instantiating ``cls`` — so
    params dataclasses that enforce a non-empty value in
    ``__post_init__`` (e.g. ``ValidatorParams.pubkey``) don't have to
    carry a sentinel default just to keep introspection working. Fields
    without a default fall back to ``None``. Values are coerced through
    ``_to_jsonable`` so dataclass defaults that aren't JSON-native
    (enums, Path, etc.) don't break the contract response.
    """
    if not is_dataclass(cls):
        raise TypeError(f"defaults_for_dataclass expected a dataclass, got {cls!r}")
    out: dict[str, Any] = {}
    for f in fields(cls):
        if f.default is not MISSING:
            value = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            value = f.default_factory()  # type: ignore[misc]
        else:
            value = None
        out[f.name] = _to_jsonable(value)
    return out


def schema_and_defaults(cls: type) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convenience wrapper returning ``(schema, defaults)`` for ``cls``."""
    return schema_for_dataclass(cls), defaults_for_dataclass(cls)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return str(value)
