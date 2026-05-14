#!/usr/bin/env python3
"""Determinism check for the simulation engine.

For each spec passed on the command line (or the canonical
``tests.golden.harness.GOLDEN_SPECS`` set when none are given), runs
``defi_sim.engine.api.run_simulation`` in two **separate** Python processes
with **different** ``PYTHONHASHSEED`` values and diffs the resulting event
stream + final result.

Two processes (not two calls in one process) is the point — Python's string
``hash()`` is randomized per process, so a single-process loop hides the
PYTHONHASHSEED-dependent class of nondeterminism the postgres migration plan
explicitly flags as risk #4.

Exits 0 if every spec is byte-identical across runs, 1 otherwise. Prints the
first ~20 diff lines for any spec that disagrees so the failure mode is
inspectable.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


WORKER = r"""
import json, sys, os
from defi_sim.engine.api import run_simulation

spec = json.load(open(sys.argv[1]))
result = run_simulation(spec)

def jsonable(obj):
    if hasattr(obj, '__dict__'):
        return {k: jsonable(v) for k, v in obj.__dict__.items() if not k.startswith('_')}
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if hasattr(obj, 'name') and hasattr(obj, 'value') and not isinstance(obj, (str, int, float, bool, bytes)):
        return f"<enum:{type(obj).__name__}.{obj.name}>"
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return repr(obj)

events_path = sys.argv[2]
result_path = sys.argv[3]

# Pull events out of the engine via re-run with a recording bus.
from defi_sim.engine.api import build_engine
from defi_sim.engine.events import EventBus
bus = EventBus(record_history=True)
engine = build_engine(spec, event_bus=bus)
engine.run()
events = [
    {
        "type": (e.type.name if hasattr(e.type, 'name') else str(e.type)),
        "round": e.round,
        "timestamp": e.timestamp,
        "event_id": e.event_id,
        "data": jsonable(e.data),
    }
    for e in bus._history
]
with open(events_path, 'w') as f:
    json.dump(events, f, sort_keys=True, default=repr)
with open(result_path, 'w') as f:
    json.dump(jsonable(result), f, sort_keys=True, default=repr)
"""


def run_once(spec_path: Path, hashseed: str, tmp_root: Path, tag: str) -> tuple[Path, Path]:
    events_path = tmp_root / f"events.{tag}.json"
    result_path = tmp_root / f"result.{tag}.json"
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = hashseed
    proc = subprocess.run(
        [sys.executable, "-c", WORKER, str(spec_path), str(events_path), str(result_path)],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"[{tag}] worker failed (exit {proc.returncode})", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(2)
    return events_path, result_path


def hash_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def diff_preview(a: Path, b: Path, n: int = 20) -> list[str]:
    al = a.read_text().splitlines()
    bl = b.read_text().splitlines()
    out = []
    for i, (x, y) in enumerate(zip(al, bl)):
        if x != y:
            out.append(f"  line {i}: A={x[:200]!r}")
            out.append(f"  line {i}: B={y[:200]!r}")
            if len(out) >= n:
                break
    if not out and len(al) != len(bl):
        out.append(f"  length differs: A={len(al)} B={len(bl)}")
    return out


def collect_specs(argv: list[str], tmp_root: Path) -> list[Path]:
    if argv:
        return [Path(p).resolve() for p in argv]
    # Default: dump the canonical GOLDEN_SPECS set to temp spec.json files.
    # Same specs the golden harness uses, so determinism and behavioural
    # equivalence guard the same input space.
    sys.path.insert(0, str(REPO_ROOT))
    from tests.golden.harness import GOLDEN_SPECS  # noqa: E402

    specs_dir = tmp_root / "specs"
    specs_dir.mkdir(exist_ok=True)
    out: list[Path] = []
    for golden in GOLDEN_SPECS:
        path = specs_dir / f"{golden.name}.json"
        path.write_text(json.dumps(golden.spec), encoding="utf-8")
        out.append(path)
    return out


def main() -> int:
    tmp_root = REPO_ROOT / ".determinism-tmp"
    tmp_root.mkdir(exist_ok=True)

    specs = collect_specs(sys.argv[1:], tmp_root)
    if not specs:
        print("no specs to check", file=sys.stderr)
        return 2

    overall_ok = True
    for spec in specs:
        label = spec.parent.name if spec.name == "spec.json" else spec.stem
        print(f"\n=== {label} ({spec}) ===")
        a_ev, a_res = run_once(spec, "1", tmp_root, "A")
        b_ev, b_res = run_once(spec, "2", tmp_root, "B")

        ev_match = hash_file(a_ev) == hash_file(b_ev)
        res_match = hash_file(a_res) == hash_file(b_res)
        print(f"  events:  {'OK' if ev_match else 'DIFF'}  (A={hash_file(a_ev)[:12]}  B={hash_file(b_ev)[:12]})")
        print(f"  result:  {'OK' if res_match else 'DIFF'}  (A={hash_file(a_res)[:12]}  B={hash_file(b_res)[:12]})")

        if not ev_match:
            print("  --- event diff preview ---")
            for line in diff_preview(a_ev, b_ev):
                print(line)
        if not res_match:
            print("  --- result diff preview ---")
            for line in diff_preview(a_res, b_res):
                print(line)

        overall_ok = overall_ok and ev_match and res_match

    print()
    print("DETERMINISTIC" if overall_ok else "NONDETERMINISTIC")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
