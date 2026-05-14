"""Behavioural-equivalence guardrail for the Postgres artifact store.

Replays every canonical spec end-to-end against the live code, normalises the
responses, and compares them to the committed captures in ``tests/golden/``.
Any diff means the user-facing API contract changed — which is exactly what
the migration must not do.

The captures were originally generated against the SQLite+filesystem store
that ``LocalArtifactStore`` retired; they are the cross-backend equivalence
contract.

If a golden is intentionally regenerated, do it via
``python scripts/capture_goldens.py`` and commit the diff alongside the
behavioural change. Do **not** regenerate just to make this test pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.golden.harness import (
    GOLDEN_SPECS,
    GoldenSpec,
    golden_dir,
    normalise_capture,
    read_captures,
    run_spec_and_capture,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("golden", GOLDEN_SPECS, ids=lambda g: g.name)
def test_golden_matches(client: TestClient, golden: GoldenSpec) -> None:
    out_dir = golden_dir(REPO_ROOT, golden.name)
    if not out_dir.exists():
        pytest.skip(
            f"no goldens at {out_dir}; run scripts/capture_goldens.py to bootstrap"
        )
    # Re-normalise the on-disk goldens so harness improvements (e.g. the
    # whole-number-float → int collapse) apply to both sides of the diff.
    expected = normalise_capture(read_captures(out_dir))

    captured = normalise_capture(run_spec_and_capture(client, golden.spec))

    missing = set(expected) - set(captured)
    extra = set(captured) - set(expected)
    assert not missing, f"missing captures: {sorted(missing)}"
    assert not extra, f"unexpected captures: {sorted(extra)}"

    for label in sorted(expected):
        exp_json = json.dumps(expected[label], indent=2, sort_keys=True)
        got_json = json.dumps(captured[label], indent=2, sort_keys=True)
        assert got_json == exp_json, (
            f"golden mismatch on {golden.name}/{label}.json "
            f"(see scripts/capture_goldens.py)"
        )
