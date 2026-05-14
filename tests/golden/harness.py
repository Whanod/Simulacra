"""Golden-file capture/compare harness for the Postgres migration.

The migration replaces SQLite + filesystem JSON with Postgres. The user-facing
contract — every API response a chart consumes — must remain identical. This
module is the single source of truth for that contract:

* :data:`GOLDEN_SPECS` enumerates the canonical specs (small enough to run in
  a few seconds; complex enough to exercise the interesting code paths).
* :func:`run_spec_and_capture` drives a TestClient through the full run+read
  cycle and returns every response keyed by an endpoint label.
* :func:`normalise_payload` strips out variable fields (run_ids, timestamps,
  floats past a fixed precision) so captures compare byte-equal across runs.
* The CLI in ``scripts/capture_goldens.py`` writes the normalised captures to
  ``tests/golden/<spec>/``; the diff test in ``tests/api/test_goldens.py``
  reads them and asserts equality.

Discipline rule from the migration plan: if a golden diffs, it's a
regression to investigate — not a fixture to regenerate. Regenerate only when
the user-facing contract is intentionally changing.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# ── canonical specs ──────────────────────────────────────────────────────────
#
# Small + deterministic. Each must complete in under ~5s on a laptop so the
# golden suite stays a snappy guardrail rather than a CI tax.

NOISE_BASELINE_SPEC: dict[str, Any] = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
        "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 0.2},
            "initial_balances": {"USDC": 1_000_000_000},
        }
    ],
    "num_rounds": 20,
    "snapshot_interval": 1,
    "seed": 42,
}

SANDWICH_SPEC: dict[str, Any] = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
        "params": {"initial_liquidity": 2_000_000, "collateral_token": "USDC"},
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 0.25},
            "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
        },
        {
            "type": "manipulator",
            "agent_id": "sandwich-1",
            "params": {"collateral": "USDC"},
            "initial_balances": {"USDC": 1_000_000_000},
        },
        {
            "type": "passive_lp",
            "agent_id": "lp-1",
            "params": {"collateral": "USDC"},
            "initial_balances": {"USDC": 2_000_000_000},
        },
    ],
    "num_rounds": 15,
    "snapshot_interval": 1,
    "seed": 7,
}


@dataclass(frozen=True)
class GoldenSpec:
    name: str
    spec: dict[str, Any]


GOLDEN_SPECS: list[GoldenSpec] = [
    GoldenSpec("noise-baseline", NOISE_BASELINE_SPEC),
    GoldenSpec("sandwich-stress", SANDWICH_SPEC),
]


# ── normalisation ────────────────────────────────────────────────────────────

# Variable fields per capture, masked to deterministic placeholders:
#
# * Anything that's a UUID — run_ids, sweep_ids, report_ids, snapshot_ids,
#   correlation prefixes. We mask both bare string values and dict KEYS.
# * Any key ending in ``_at`` — timestamp strings.
# * Floats canonicalised to 12 sig figs (digits arg clamped to 15 so
#   ``round`` stays in well-defined territory for subnormals).
# * NaN/Inf collapsed to fixed sentinels so captures are comparable across
#   runs that might emit different non-finite floats.

_ID_KEYS = {
    "run_id",
    "simulation_id",
    "source_run_id",
    "source_snapshot_id",
    "snapshot_id",
    "sweep_id",
    "report_id",
}
_TIMESTAMP_KEYS_SUFFIX = "_at"
_FLOAT_SIG_FIGS = 12

_FULL_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def _canonicalise_float(value: float) -> Any:
    # Caveat on the whole-number collapse below: a previously-integer field
    # that starts arriving as a non-integer float close enough to round to a
    # whole number under the sig-figs clamp will be silently collapsed back
    # to an int and look unchanged in the diff. Narrow corner — the goldens
    # cover deterministic specs where the engine emits the same shape each
    # run — but worth knowing if a "no diff" result ever feels suspicious.
    if math.isnan(value):
        return "<nan>"
    if math.isinf(value):
        return "<+inf>" if value > 0 else "<-inf>"
    if value == 0.0:
        return 0
    digits = _FLOAT_SIG_FIGS - int(math.floor(math.log10(abs(value)))) - 1
    # Round's behaviour past ~15 digits is implementation-defined for
    # binary floats; clamp so we always land on a defined codepath.
    digits = max(min(digits, 15), -15)
    rounded = round(value, digits)
    # JSON has no int/float distinction, but ``json.dumps`` does — Postgres
    # stores timestamps as DOUBLE PRECISION and returns ``1.0`` where the
    # legacy SQLite path preserved engine-emitted ``1`` (int). Collapse
    # whole-number floats to ints so the two backends serialise identically.
    if isinstance(rounded, float) and rounded.is_integer():
        return int(rounded)
    return rounded


def _collect_ids(payload: Any, ids: set[str]) -> None:
    """Walk ``payload`` and accumulate every value that lives under an
    id-shaped key into ``ids``. Run IDs in this project are 12-char hex —
    too permissive to detect by shape alone — so we anchor discovery on
    the canonical key names instead."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key in _ID_KEYS and isinstance(value, str) and value:
                ids.add(value)
            _collect_ids(value, ids)
    elif isinstance(payload, list):
        for item in payload:
            _collect_ids(item, ids)


def _build_id_sub(ids: set[str], run_id_map: dict[str, str]) -> Any:
    """Build a regex that matches every known id plus generic UUIDs. The
    callback assigns deterministic labels in discovery order — so as long as
    captures walk the same shape, labels are stable across runs."""
    # Discovery order is critical: sort by length DESC so longer ids take
    # precedence over hex-prefix collisions with shorter ones.
    sorted_ids = sorted(ids, key=lambda s: -len(s))
    parts = [re.escape(i) for i in sorted_ids]
    parts.append(_FULL_UUID_RE.pattern)
    combined = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    def sub(match: re.Match[str]) -> str:
        return run_id_map.setdefault(match.group(0), f"<run-{len(run_id_map)}>")

    return combined, sub


def _normalise_value(payload: Any, sub_re, sub_fn, run_id_map: dict[str, str]) -> Any:
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            new_key = key
            if isinstance(key, str) and sub_re is not None:
                new_key = sub_re.sub(sub_fn, key)
            if isinstance(key, str) and key.endswith(_TIMESTAMP_KEYS_SUFFIX) and isinstance(value, str):
                out[new_key] = "<masked-timestamp>"
            else:
                out[new_key] = _normalise_value(value, sub_re, sub_fn, run_id_map)
        return out
    if isinstance(payload, list):
        return [_normalise_value(item, sub_re, sub_fn, run_id_map) for item in payload]
    if isinstance(payload, str):
        return sub_re.sub(sub_fn, payload) if sub_re is not None else payload
    if isinstance(payload, float):
        return _canonicalise_float(payload)
    return payload


def normalise_payload(payload: Any, run_id_map: dict[str, str]) -> Any:
    """Walk ``payload`` and replace variable fields with stable placeholders.

    Two passes: first collect every id-shaped value (anchored on key names —
    short hex strings can't be detected by shape), then sweep with a single
    regex that handles ids embedded anywhere in any string. Dict keys are
    masked too. Timestamps become a fixed sentinel. Floats canonicalised to
    12 sig figs.
    """
    ids: set[str] = set()
    _collect_ids(payload, ids)
    sub_re, sub_fn = _build_id_sub(ids, run_id_map)
    return _normalise_value(payload, sub_re, sub_fn, run_id_map)


# ── capture ──────────────────────────────────────────────────────────────────

# Endpoints to capture. Tuples of (label, function-of-run_id → response dict).
# Each fn receives the TestClient and returns a (status, body) pair.
EndpointFn = Callable[[Any, str], tuple[int, Any]]


def _get(client: Any, path: str) -> tuple[int, Any]:
    resp = client.get(path)
    return resp.status_code, resp.json()


def run_spec_and_capture(client: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Drive ``client`` through one spec end-to-end; return labelled captures.

    The keys are stable filenames (without extension). Values are the JSON
    response bodies — pre-normalisation; callers normalise before writing.
    """
    resp = client.post("/simulations/run", json=spec)
    assert resp.status_code == 200, f"run failed: {resp.status_code} {resp.text[:200]}"
    body = resp.json()
    run_id = body["run_id"]

    captures: dict[str, Any] = {"run": body}

    captures["run_meta"] = _get(client, f"/runs/{run_id}")[1]
    captures["run_spec"] = _get(client, f"/runs/{run_id}/spec")[1]
    # Phase 5.3 retired the ``GET /runs/{id}/result`` endpoint. The composer
    # ``store.get_run_result`` still produces the legacy shape for in-process
    # callers (share / reports / embed); keep the golden file pinned to that
    # composed shape so a future regression in the composer is caught.
    from defi_sim_api.backend.store import get_artifact_store
    composed = get_artifact_store().get_run_result(run_id)
    captures["run_result"] = {"run_id": run_id, "result": composed}
    captures["run_rounds"] = _get(client, f"/runs/{run_id}/rounds")[1]
    captures["run_events"] = _get(client, f"/runs/{run_id}/events?limit=10000")[1]

    # Round snapshots: first, middle, last.
    rounds_list = captures["run_rounds"].get("available_rounds") or []
    if rounds_list:
        picks = sorted({rounds_list[0], rounds_list[len(rounds_list) // 2], rounds_list[-1]})
        for r in picks:
            captures[f"round_{r:04d}"] = _get(client, f"/runs/{run_id}/rounds/{r}")[1]

    # Agent timelines for every declared agent.
    for agent in spec.get("agents", []):
        agent_id = agent.get("agent_id")
        if not agent_id:
            continue
        captures[f"timeline_{agent_id}"] = _get(
            client, f"/runs/{run_id}/agents/{agent_id}/timeline"
        )[1]

    captures["named_snapshots"] = _get(client, f"/runs/{run_id}/snapshots")[1]
    captures["runs_list"] = _get(client, "/runs")[1]

    return captures


def normalise_capture(captures: dict[str, Any]) -> dict[str, Any]:
    run_id_map: dict[str, str] = {}
    return {key: normalise_payload(value, run_id_map) for key, value in captures.items()}


def golden_dir(repo_root: Path, spec_name: str) -> Path:
    return repo_root / "tests" / "golden" / spec_name


def write_captures(out_dir: Path, captures: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, body in captures.items():
        path = out_dir / f"{label}.json"
        path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")


def read_captures(out_dir: Path) -> dict[str, Any]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(out_dir.glob("*.json"))
    }


__all__ = [
    "GOLDEN_SPECS",
    "GoldenSpec",
    "golden_dir",
    "normalise_capture",
    "normalise_payload",
    "read_captures",
    "run_spec_and_capture",
    "write_captures",
]
