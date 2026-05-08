"""Helpers for applying nested patches into RunSpec-like payloads."""

from __future__ import annotations

import copy
import re
from typing import Any


_SEGMENT_RE = re.compile(r"([^.[]+)(?:\[(.+?)\])?")


class PatchPathError(ValueError):
    """Raised when a spec patch path is invalid."""


def _parse_selector(raw: str) -> int | tuple[str, str]:
    if raw.isdigit():
        return int(raw)
    if "=" in raw:
        key, value = raw.split("=", 1)
        return key, value
    raise PatchPathError(f"Unsupported list selector [{raw}]")


def parse_patch_path(path: str) -> list[str | int | tuple[str, str]]:
    segments: list[str | int | tuple[str, str]] = []
    for chunk in path.split("."):
        if not chunk:
            raise PatchPathError(f"Invalid empty path segment in {path!r}")
        match = _SEGMENT_RE.fullmatch(chunk)
        if match is None:
            raise PatchPathError(f"Invalid patch segment {chunk!r}")
        segments.append(match.group(1))
        selector = match.group(2)
        if selector is not None:
            segments.append(_parse_selector(selector))
    return segments


def _resolve_list_segment(target: list[Any], segment: int | tuple[str, str], path: str) -> int:
    if isinstance(segment, int):
        if segment < 0 or segment >= len(target):
            raise PatchPathError(f"List index {segment} out of range for {path!r}")
        return segment
    key, expected = segment
    for index, item in enumerate(target):
        if isinstance(item, dict) and str(item.get(key)) == expected:
            return index
    raise PatchPathError(f"No list item matching {key}={expected!r} for {path!r}")


def _normalize_segments(path: str | list[Any]) -> tuple[list[Any], str]:
    if isinstance(path, str):
        return parse_patch_path(path), path
    normalized: list[Any] = []
    pretty: list[str] = []
    for segment in path:
        if isinstance(segment, dict):
            if "index" in segment:
                normalized.append(int(segment["index"]))
                pretty.append(f"[{int(segment['index'])}]")
                continue
            match = segment.get("match")
            if isinstance(match, dict) and len(match) == 1:
                key, value = next(iter(match.items()))
                normalized.append((str(key), str(value)))
                pretty.append(f"[{key}={value}]")
                continue
            raise PatchPathError(f"Unsupported structured segment {segment!r}")
        normalized.append(segment)
        pretty.append(str(segment))
    return normalized, ".".join(pretty)


def apply_spec_patch(
    spec: dict[str, Any],
    path: str | list[Any],
    value: Any,
) -> dict[str, Any]:
    segments, pretty = _normalize_segments(path)
    patched = copy.deepcopy(spec)
    cursor: Any = patched
    for position, segment in enumerate(segments):
        is_last = position == len(segments) - 1
        if isinstance(cursor, list):
            if not isinstance(segment, (int, tuple)):
                raise PatchPathError(f"Expected list selector in {pretty!r}, got {segment!r}")
            index = _resolve_list_segment(cursor, segment, pretty)
            if is_last:
                cursor[index] = value
                return patched
            cursor = cursor[index]
            continue

        if not isinstance(cursor, dict):
            raise PatchPathError(f"Cannot descend through non-container at {pretty!r}")

        if isinstance(segment, tuple):
            raise PatchPathError(f"List selector used on dict path {pretty!r}")

        key = str(segment)
        if is_last:
            cursor[key] = value
            return patched

        if key not in cursor:
            raise PatchPathError(f"Unknown patch path {pretty!r}: missing {key!r}")
        cursor = cursor[key]

    raise PatchPathError(f"Empty patch path {pretty!r}")


def apply_spec_patches(
    spec: dict[str, Any],
    patches: dict[str, Any] | list[dict[str, Any]],
) -> dict[str, Any]:
    patched = copy.deepcopy(spec)
    if isinstance(patches, dict):
        items = [{"path": path, "value": value} for path, value in patches.items()]
    else:
        items = list(patches)
    for item in items:
        patched = apply_spec_patch(patched, item["path"], item["value"])
    return patched
