"""Calibration dashboard endpoints (PRD US-004 line 787).

``GET /v1/calibration/corpus`` returns a per-slot scoreboard for the calibration
corpus committed under ``solana-plans/calibration/corpus/<slot>/``: each slot's
manifest (programs, expected metrics), the threshold table, the most recent
persisted replay run that targeted the slot (run id + timestamp + per-metric
error if available), and a per-metric trend marker (improving / regressing /
stable / no_history) computed from the two most-recent replay runs.

The dashboard at ``/calibration`` consumes this endpoint to render the
PRD-line-787 deliverables (per-corpus-slot scoreboard, per-metric trend,
last-run timestamp per slot). The endpoint is read-only and does not touch
archival RPC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

from defi_sim.calibration.thresholds import THRESHOLDS_YAML_PATH, load_thresholds
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim_api.backend.store import get_artifact_store

router = APIRouter(prefix="/v1/calibration", tags=["calibration"])


def _read_manifest(slot_dir: Path) -> dict[str, Any]:
    manifest_path = slot_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _list_corpus_slots() -> list[tuple[int, Path]]:
    root = corpus_root()
    if not root.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            slot = int(entry.name)
        except ValueError:
            continue
        out.append((slot, entry))
    out.sort(key=lambda pair: pair[0])
    return out


def _replay_runs_for_slot(slot: int) -> list[dict[str, Any]]:
    """Return persisted replay runs whose slot_range covers ``slot``.

    Sorted newest first. Reads the artifact store directly; live runs without
    a persisted summary are skipped.
    """

    store = get_artifact_store()
    matches: list[dict[str, Any]] = []
    for record in store.list_runs(limit=500, offset=0):
        if record is None:
            continue
        summary = record.get("summary") or {}
        if summary.get("kind") != "replay":
            continue
        slot_range = summary.get("slot_range")
        if not (
            isinstance(slot_range, (list, tuple))
            and len(slot_range) == 2
            and isinstance(slot_range[0], int)
            and isinstance(slot_range[1], int)
        ):
            continue
        if slot_range[0] <= slot <= slot_range[1]:
            matches.append(record)
    return matches


def _per_metric_error_from(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary = record.get("summary") or {}
    diff = summary.get("replay_diff")
    if not isinstance(diff, dict):
        return {}
    bands = diff.get("per_metric_error")
    if not isinstance(bands, dict):
        bands = diff
    out: dict[str, dict[str, Any]] = {}
    for key, value in bands.items():
        if not isinstance(value, dict):
            continue
        abs_err = value.get("abs_error")
        if abs_err is None:
            continue
        out[key] = {
            "abs_error": abs_err,
            "predicted": value.get("predicted"),
            "actual": value.get("actual"),
            "supported": bool(value.get("supported", True)),
        }
    return out


def _trend(latest: float, previous: float) -> tuple[str, float]:
    delta = latest - previous
    if abs(delta) < 1e-12:
        return "stable", 0.0
    return ("improving" if delta < 0 else "regressing", delta)


@router.get(
    "/corpus",
    summary="Per-slot calibration scoreboard with thresholds, last-run timestamp, and per-metric trend",
)
def get_corpus() -> dict[str, Any]:
    thresholds = load_thresholds()
    threshold_payload = [
        {
            "metric": t.metric,
            "threshold_relative": t.threshold_relative,
            "threshold_absolute": t.threshold_absolute,
        }
        for t in thresholds.values()
    ]

    slots_payload: list[dict[str, Any]] = []
    for slot, slot_dir in _list_corpus_slots():
        manifest = _read_manifest(slot_dir)
        runs = _replay_runs_for_slot(slot)
        latest = runs[0] if runs else None
        previous = runs[1] if len(runs) > 1 else None

        latest_bands = _per_metric_error_from(latest) if latest else {}
        previous_bands = _per_metric_error_from(previous) if previous else {}

        trends: list[dict[str, Any]] = []
        for metric, latest_band in sorted(latest_bands.items()):
            prev_band = previous_bands.get(metric)
            if prev_band is None:
                trends.append(
                    {
                        "metric": metric,
                        "direction": "no_history",
                        "delta": None,
                        "latest_abs_error": latest_band["abs_error"],
                    }
                )
                continue
            direction, delta = _trend(latest_band["abs_error"], prev_band["abs_error"])
            trends.append(
                {
                    "metric": metric,
                    "direction": direction,
                    "delta": delta,
                    "latest_abs_error": latest_band["abs_error"],
                    "previous_abs_error": prev_band["abs_error"],
                }
            )

        last_run: dict[str, Any] | None = None
        if latest is not None:
            last_run = {
                "run_id": latest.get("run_id"),
                "created_at": latest.get("created_at"),
                "status": latest.get("status"),
                "mainnet_accuracy_claim": (latest.get("summary") or {}).get(
                    "mainnet_accuracy_claim"
                ),
                "replay_kind": (latest.get("summary") or {}).get("replay_kind"),
                "per_metric_error": latest_bands,
            }

        expected = manifest.get("expected") if isinstance(manifest, dict) else None
        category = manifest.get("category") if isinstance(manifest, dict) else None
        slots_payload.append(
            {
                "slot": slot,
                "programs": list(manifest.get("programs") or []),
                "expected": expected if isinstance(expected, dict) else {},
                "category": str(category) if isinstance(category, str) else None,
                "last_run": last_run,
                "trend": trends,
                "run_count": len(runs),
            }
        )

    return {
        "corpus_root": str(corpus_root()),
        "thresholds_yaml": str(THRESHOLDS_YAML_PATH),
        "thresholds": threshold_payload,
        "slots": slots_payload,
    }
