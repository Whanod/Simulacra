"""Claim-gated Phase 2 definition-of-done test.

Plan ref: solana-plans/phase-2.md Phase DoD assertion.

This file intentionally does not run live replay, fork, calibration, or
benchmark jobs in the default integration lane. Instead, it is a deterministic
admission gate: while Phase 2 stories remain pending it is dormant; once Phase
2 completion is claimed, it requires committed evidence for each Phase 2 DoD
capability. The slow checks that create those evidence files belong in the
``calibration`` and ``forked_state`` lanes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_2_PATH = REPO_ROOT / "solana-plans" / "phase-2.md"
COMPLETION_MARKER_PATH = REPO_ROOT / "solana-plans" / "phase-2-complete.md"
DOD_EVIDENCE_PATH = REPO_ROOT / "solana-plans" / "phase-2-dod-evidence.json"

GATING_STATUSES = {"done"}
COMPLETION_MARKER_STATUSES = {"complete", "completed", "done"}

_STORY_HEADER_RE = re.compile(r"^###\s+(US-\d+)(?::\s*(.+?))?\s*$")
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class Phase2Story:
    story_id: str
    title: str
    status: str


@dataclass(frozen=True)
class RequiredEvidence:
    key: str
    description: str
    needs_account_state: bool = False


REQUIRED_EVIDENCE: tuple[RequiredEvidence, ...] = (
    RequiredEvidence(
        key="replay_counterfactual_tip",
        description="historical replay with a counterfactual tip",
    ),
    RequiredEvidence(
        key="predicted_vs_actual_per_chart_error",
        description="predicted-vs-actual per-chart replay error",
    ),
    RequiredEvidence(
        key="selective_fork_target_slot",
        description="selective fork at a target slot",
        needs_account_state=True,
    ),
    RequiredEvidence(
        key="calibrated_bundle_landing_probability",
        description="calibrated bundle landing probability",
        needs_account_state=True,
    ),
    RequiredEvidence(
        key="benchmark_run_path",
        description="one-click benchmark run path",
    ),
)


def _walk_phase_2_stories(text: str) -> list[Phase2Story]:
    """Return Phase 2 US story status records from ``phase-2.md`` text."""
    stories: list[Phase2Story] = []
    current_id: str | None = None
    current_title = ""
    current_status = ""
    have_status = False

    def flush() -> None:
        if current_id is not None:
            stories.append(
                Phase2Story(
                    story_id=current_id,
                    title=current_title,
                    status=current_status,
                )
            )

    for line in text.splitlines():
        header = _STORY_HEADER_RE.match(line)
        if header:
            flush()
            current_id = header.group(1)
            current_title = (header.group(2) or "").strip()
            current_status = ""
            have_status = False
            continue
        if current_id is not None and not have_status:
            status_match = _STATUS_RE.match(line)
            if status_match:
                current_status = status_match.group(1).strip().lower()
                have_status = True
    flush()
    return stories


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return value[:idx]
    return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter_scalars(text: str) -> dict[str, str]:
    """Parse simple leading YAML frontmatter scalars without a YAML dependency."""
    if not text.startswith("---"):
        return {}
    first_nl = text.find("\n")
    if first_nl == -1:
        return {}
    end = text.find("\n---", first_nl)
    if end == -1:
        return {}
    frontmatter: dict[str, str] = {}
    for raw_line in text[first_nl + 1 : end].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        frontmatter[key.strip()] = _unquote(_strip_inline_comment(value).strip())
    return frontmatter


def _marker_claims_completion(path: Path) -> bool:
    if not path.is_file():
        return False
    frontmatter = _parse_frontmatter_scalars(path.read_text(encoding="utf-8"))
    status = frontmatter.get("status", "").strip().lower()
    phase = frontmatter.get("phase", "").strip()
    return phase == "2" and status in COMPLETION_MARKER_STATUSES


def _all_phase_2_stories_done(text: str) -> bool:
    stories = _walk_phase_2_stories(text)
    return bool(stories) and all(story.status in GATING_STATUSES for story in stories)


def _completion_claimed(phase_2_text: str, marker_path: Path) -> bool:
    return _all_phase_2_stories_done(phase_2_text) or _marker_claims_completion(
        marker_path
    )


def _load_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{path.relative_to(REPO_ROOT)} is not valid JSON: {exc}")
    assert isinstance(payload, dict), (
        f"{path.relative_to(REPO_ROOT)} must contain a JSON object, got "
        f"{type(payload).__name__}."
    )
    return payload


def _evidence_items(evidence: dict[str, Any]) -> dict[str, Any]:
    items = evidence.get("items", {})
    if not isinstance(items, dict):
        return {}
    return items


def _artifact_path_violations(
    record: dict[str, Any], *, evidence_root: Path, label: str
) -> list[str]:
    raw_paths = record.get("artifact_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        return [f"{label}: artifact_paths must name committed proof artifacts"]

    violations: list[str] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            violations.append(f"{label}: artifact_paths contains an empty path")
            continue
        path = Path(raw_path)
        if path.is_absolute():
            violations.append(
                f"{label}: artifact path {raw_path!r} must be repo-relative"
            )
            continue
        if not (evidence_root / path).is_file():
            violations.append(f"{label}: missing artifact {raw_path!r}")
    return violations


def _artifact_payload_violations(
    record: dict[str, Any], *, evidence_root: Path, label: str
) -> list[str]:
    raw_paths = record.get("artifact_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        return []

    violations: list[str] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = Path(raw_path)
        if path.is_absolute():
            continue
        artifact_path = evidence_root / path
        if not artifact_path.is_file():
            continue
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            violations.append(f"{label}: artifact {raw_path!r} is not valid JSON: {exc}")
            continue
        if not isinstance(payload, dict) or not payload:
            violations.append(
                f"{label}: artifact {raw_path!r} must contain non-empty JSON proof"
            )
            continue
        proof = _proof_payload_for_key(payload, label)
        if proof is None:
            violations.append(
                f"{label}: artifact {raw_path!r} must include proof for {label}"
            )
            continue
        violations.extend(_proof_payload_claim_violations(proof, label, raw_path))
    return violations


def _proof_payload_for_key(payload: dict[str, Any], label: str) -> dict[str, Any] | None:
    direct = payload.get(label)
    if isinstance(direct, dict):
        return direct
    items = payload.get("items")
    if isinstance(items, dict) and isinstance(items.get(label), dict):
        return items[label]
    if payload.get("evidence_key") == label or payload.get("kind") == label:
        return payload
    return None


def _artifact_counterfactuals(proof: dict[str, Any]) -> list[Any]:
    for key in ("counterfactuals",):
        value = proof.get(key)
        if isinstance(value, list):
            return value
    for parent_key in ("summary", "spec", "result"):
        parent = proof.get(parent_key)
        if isinstance(parent, dict) and isinstance(parent.get("counterfactuals"), list):
            return parent["counterfactuals"]
    return []


def _artifact_per_metric_error(proof: dict[str, Any]) -> Any:
    if "per_metric_error" in proof:
        return proof["per_metric_error"]
    replay_diff = proof.get("replay_diff")
    if isinstance(replay_diff, dict) and "per_metric_error" in replay_diff:
        return replay_diff["per_metric_error"]
    result = proof.get("result")
    if isinstance(result, dict):
        replay_diff = result.get("replay_diff")
        if isinstance(replay_diff, dict):
            return replay_diff.get("per_metric_error")
    return None


def _proof_payload_claim_violations(
    proof: dict[str, Any], label: str, artifact_path: str
) -> list[str]:
    prefix = f"{label}: artifact {artifact_path!r}"
    if label == "replay_counterfactual_tip":
        if not any(
            isinstance(item, dict) and item.get("kind") == "TipReplaceCounterfactual"
            for item in _artifact_counterfactuals(proof)
        ):
            return [f"{prefix} must prove a TipReplaceCounterfactual replay"]
    elif label == "predicted_vs_actual_per_chart_error":
        bands = _artifact_per_metric_error(proof)
        if not isinstance(bands, dict) or not bands:
            return [f"{prefix} must contain replay_diff.per_metric_error"]
    elif label == "selective_fork_target_slot":
        historical = proof.get("historical_account_state")
        if not isinstance(historical, dict):
            historical = proof
        if not isinstance(historical.get("as_of_slot"), int):
            return [f"{prefix} must prove an integer historical as_of_slot"]
        if not str(historical.get("provenance") or "").strip():
            return [f"{prefix} must include account-state provenance"]
        if len(str(historical.get("raw_account_sha256") or "").strip()) < 16:
            return [f"{prefix} must include raw_account_sha256"]
    elif label == "calibrated_bundle_landing_probability":
        if proof.get("calibrated") is not True:
            return [f"{prefix} must prove calibrated=true"]
        if proof.get("mainnet_accuracy_claim") is not True:
            return [f"{prefix} must prove mainnet_accuracy_claim=true"]
        probability = proof.get("landing_probability")
        if not isinstance(probability, int | float) or not 0 <= probability <= 1:
            return [f"{prefix} must include landing_probability in [0, 1]"]
        if not str(proof.get("calibration_source") or "").strip():
            return [f"{prefix} must include calibration_source"]
    elif label == "benchmark_run_path":
        if not str(proof.get("run_id") or "").strip():
            return [f"{prefix} must include run_id"]
        if not str(proof.get("benchmark_path") or proof.get("benchmark_url") or "").strip():
            return [f"{prefix} must include benchmark_path or benchmark_url"]
    return []


def _common_record_violations(
    record: Any, required: RequiredEvidence, *, evidence_root: Path
) -> list[str]:
    label = required.key
    if not isinstance(record, dict):
        return [f"{label}: evidence record must be an object"]
    violations = _artifact_path_violations(
        record, evidence_root=evidence_root, label=label
    )
    violations.extend(
        _artifact_payload_violations(record, evidence_root=evidence_root, label=label)
    )
    if record.get("fixture_kind") != "calibration":
        violations.append(
            f"{label}: fixture_kind must be 'calibration'; development fixtures "
            "cannot satisfy the Phase 2 DoD"
        )
    return violations


def _has_tip_counterfactual(record: dict[str, Any]) -> bool:
    counterfactuals = record.get("counterfactuals")
    if not isinstance(counterfactuals, list):
        return False
    return any(
        isinstance(item, dict) and item.get("kind") == "TipReplaceCounterfactual"
        for item in counterfactuals
    )


def _metric_error_violations(record: dict[str, Any]) -> list[str]:
    bands = record.get("per_metric_error")
    if not isinstance(bands, dict) or not bands:
        return [
            "predicted_vs_actual_per_chart_error: per_metric_error must be a "
            "non-empty metric map"
        ]
    violations: list[str] = []
    supported_count = 0
    for metric, band in bands.items():
        if not isinstance(metric, str) or not metric:
            violations.append(
                "predicted_vs_actual_per_chart_error: metric keys must be non-empty"
            )
            continue
        if not isinstance(band, dict):
            violations.append(
                f"predicted_vs_actual_per_chart_error: {metric} band must be an object"
            )
            continue
        if band.get("supported") is False:
            continue
        missing = [
            key
            for key in ("predicted", "actual", "threshold")
            if key not in band
        ]
        has_error = any(
            key in band
            for key in (
                "absolute_error",
                "relative_error",
                "abs_error",
                "rel_error",
                "error",
            )
        )
        if missing or not has_error:
            violations.append(
                f"predicted_vs_actual_per_chart_error: {metric} must include "
                "predicted, actual, threshold, and an error value"
            )
        else:
            supported_count += 1
    if supported_count == 0:
        violations.append(
            "predicted_vs_actual_per_chart_error: per_metric_error must include "
            "at least one supported metric band"
        )
    return violations


def _historical_account_state_violations(record: dict[str, Any]) -> list[str]:
    target_slot = record.get("target_slot")
    protocols = record.get("protocols")
    proof = record.get("historical_account_state")
    violations: list[str] = []
    if not isinstance(target_slot, int) or target_slot <= 0:
        violations.append("selective_fork_target_slot: target_slot must be a positive int")
    if not isinstance(protocols, list) or not protocols:
        violations.append("selective_fork_target_slot: protocols must be non-empty")
    if not isinstance(proof, dict):
        violations.append(
            "selective_fork_target_slot: account-state-blocked - missing "
            "historical_account_state proof for exact as-of-slot account bytes"
        )
        return violations
    if proof.get("as_of_slot") != target_slot:
        violations.append(
            "selective_fork_target_slot: account-state-blocked - "
            "historical_account_state.as_of_slot must match target_slot"
        )
    provenance = proof.get("provenance")
    checksum = proof.get("raw_account_sha256")
    if not isinstance(provenance, str) or not provenance.strip():
        violations.append(
            "selective_fork_target_slot: account-state-blocked - provenance is required"
        )
    if not isinstance(checksum, str) or len(checksum.strip()) < 16:
        violations.append(
            "selective_fork_target_slot: account-state-blocked - "
            "raw_account_sha256 is required"
        )
    return violations


def _bundle_landing_violations(record: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    landing_probability = record.get("landing_probability")
    if not isinstance(landing_probability, int | float) or not (
        0 <= landing_probability <= 1
    ):
        violations.append(
            "calibrated_bundle_landing_probability: landing_probability must be in [0, 1]"
        )
    if record.get("calibrated") is not True:
        violations.append(
            "calibrated_bundle_landing_probability: calibrated must be true"
        )
    if record.get("mainnet_accuracy_claim") is not True:
        violations.append(
            "calibrated_bundle_landing_probability: mainnet_accuracy_claim must be true"
        )
    if not isinstance(record.get("calibration_source"), str) or not record[
        "calibration_source"
    ].strip():
        violations.append(
            "calibrated_bundle_landing_probability: calibration_source is required"
        )
    return violations


def _benchmark_violations(record: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    path_or_url = record.get("benchmark_path") or record.get("benchmark_url")
    if not isinstance(path_or_url, str) or not path_or_url.strip():
        violations.append("benchmark_run_path: benchmark_path or benchmark_url is required")
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        violations.append("benchmark_run_path: run_id is required")
    return violations


def _phase_2_dod_violations(
    evidence: dict[str, Any], *, evidence_root: Path = REPO_ROOT
) -> list[str]:
    items = _evidence_items(evidence)
    violations: list[str] = []
    account_state_blocked: list[str] = []

    for required in REQUIRED_EVIDENCE:
        record = items.get(required.key)
        if record is None:
            message = (
                f"missing {required.key}: required evidence for "
                f"{required.description}"
            )
            violations.append(message)
            if required.needs_account_state:
                account_state_blocked.append(required.description)
            continue
        record_violations = _common_record_violations(
            record, required, evidence_root=evidence_root
        )
        if isinstance(record, dict):
            if required.key == "replay_counterfactual_tip":
                if not _has_tip_counterfactual(record):
                    record_violations.append(
                        "replay_counterfactual_tip: counterfactuals must include "
                        "TipReplaceCounterfactual"
                    )
            elif required.key == "predicted_vs_actual_per_chart_error":
                record_violations.extend(_metric_error_violations(record))
            elif required.key == "selective_fork_target_slot":
                fork_violations = _historical_account_state_violations(record)
                record_violations.extend(fork_violations)
                if fork_violations:
                    account_state_blocked.append(required.description)
            elif required.key == "calibrated_bundle_landing_probability":
                record_violations.extend(_bundle_landing_violations(record))
            elif required.key == "benchmark_run_path":
                record_violations.extend(_benchmark_violations(record))
        violations.extend(record_violations)

    if account_state_blocked:
        blocked = ", ".join(sorted(set(account_state_blocked)))
        violations.append(
            "account-state-blocked capabilities remain unresolved: "
            f"{blocked}. Complete FIX-014 with exact as-of-slot account-state "
            "evidence before claiming the Phase 2 DoD."
        )
    return violations


def _complete_evidence(*, artifact_path: str) -> dict[str, Any]:
    """Build a valid evidence payload for focused schema tests."""
    return {
        "version": 1,
        "items": {
            "replay_counterfactual_tip": {
                "fixture_kind": "calibration",
                "artifact_paths": [artifact_path],
                "slot": 420_196_842,
                "counterfactuals": [
                    {
                        "kind": "TipReplaceCounterfactual",
                        "params": {
                            "target_bundle_id": "bundle-1",
                            "new_tip_lamports": 0,
                        },
                    }
                ],
            },
            "predicted_vs_actual_per_chart_error": {
                "fixture_kind": "calibration",
                "artifact_paths": [artifact_path],
                "per_metric_error": {
                    "slot_volume": {
                        "predicted": 100.0,
                        "actual": 98.0,
                        "absolute_error": 2.0,
                        "threshold": 5.0,
                    }
                },
            },
            "selective_fork_target_slot": {
                "fixture_kind": "calibration",
                "artifact_paths": [artifact_path],
                "target_slot": 420_196_842,
                "protocols": ["Whirlpool"],
                "historical_account_state": {
                    "as_of_slot": 420_196_842,
                    "provenance": "provider export reviewed in PR",
                    "raw_account_sha256": "0123456789abcdef",
                },
            },
            "calibrated_bundle_landing_probability": {
                "fixture_kind": "calibration",
                "artifact_paths": [artifact_path],
                "landing_probability": 0.72,
                "calibrated": True,
                "mainnet_accuracy_claim": True,
                "calibration_source": "solana-plans/calibration/corpus/420196842",
            },
            "benchmark_run_path": {
                "fixture_kind": "calibration",
                "artifact_paths": [artifact_path],
                "benchmark_path": "/benchmark/420196842",
                "run_id": "run-phase-2-dod",
            },
        },
    }


def _write_complete_artifact(path: Path, evidence: dict[str, Any]) -> None:
    path.write_text(
        json.dumps({"items": evidence["items"]}, sort_keys=True),
        encoding="utf-8",
    )


def test_phase_2_done_gate_enforces_claimed_completion() -> None:
    """Dormant until Phase 2 is claimed complete, strict once claimed."""
    assert PHASE_2_PATH.is_file(), (
        f"Expected Phase 2 plan at {PHASE_2_PATH.relative_to(REPO_ROOT)}; "
        "the DoD gate has nothing to walk against without it."
    )

    phase_2_text = PHASE_2_PATH.read_text(encoding="utf-8")
    if not _completion_claimed(phase_2_text, COMPLETION_MARKER_PATH):
        return

    evidence = _load_evidence(DOD_EVIDENCE_PATH)
    violations = _phase_2_dod_violations(evidence)
    assert not violations, (
        "Phase 2 completion is claimed, but required DoD evidence is missing "
        "or invalid:\n- " + "\n- ".join(violations)
    )


def test_all_done_plan_counts_as_completion_claim() -> None:
    phase_2_text = """
### US-001: Slot ingestion
**Status:** done
### US-002: Replay
**Status:** done
"""
    assert _completion_claimed(phase_2_text, marker_path=Path("/does/not/exist"))


def test_done_marker_counts_as_completion_claim(tmp_path: Path) -> None:
    marker = tmp_path / "phase-2-complete.md"
    marker.write_text("---\nphase: 2\nstatus: complete\n---\n", encoding="utf-8")
    phase_2_text = """
### US-001: Slot ingestion
**Status:** pending
"""
    assert _completion_claimed(phase_2_text, marker_path=marker)


def test_simulated_completion_claim_fails_for_missing_evidence() -> None:
    violations = _phase_2_dod_violations({})
    assert any("replay_counterfactual_tip" in item for item in violations)
    assert any("predicted_vs_actual_per_chart_error" in item for item in violations)
    assert any("selective_fork_target_slot" in item for item in violations)
    assert any(
        "calibrated_bundle_landing_probability" in item for item in violations
    )
    assert any("benchmark_run_path" in item for item in violations)
    assert any("account-state-blocked capabilities" in item for item in violations)


def test_development_fixtures_do_not_satisfy_phase_2_done(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    evidence = _complete_evidence(artifact_path="proof.json")
    _write_complete_artifact(artifact, evidence)
    for record in evidence["items"].values():
        record["fixture_kind"] = "development"

    violations = _phase_2_dod_violations(evidence, evidence_root=tmp_path)
    assert violations
    assert all("development fixtures cannot satisfy" in item for item in violations)


def test_empty_artifacts_do_not_satisfy_phase_2_done(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    artifact.write_text("{}", encoding="utf-8")
    evidence = _complete_evidence(artifact_path="proof.json")

    violations = _phase_2_dod_violations(evidence, evidence_root=tmp_path)
    assert violations
    assert any("must contain non-empty JSON proof" in item for item in violations)


def test_complete_committed_evidence_shape_passes(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    evidence = _complete_evidence(artifact_path="proof.json")
    _write_complete_artifact(artifact, evidence)

    violations = _phase_2_dod_violations(evidence, evidence_root=tmp_path)
    assert violations == []


def test_backend_replay_diff_shape_satisfies_metric_evidence(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    evidence = _complete_evidence(artifact_path="proof.json")
    evidence["items"]["predicted_vs_actual_per_chart_error"]["per_metric_error"] = {
        "bundle_landing_rate": {
            "metric": "bundle_landing_rate",
            "predicted": 0.9,
            "actual": 1.0,
            "abs_error": 0.1,
            "rel_error": 0.1,
            "threshold": 0.05,
            "threshold_kind": "absolute",
            "supported": True,
        },
        "pool_price:SOL/USDC": {
            "metric": "pool_price:SOL/USDC",
            "predicted": 0.0,
            "actual": None,
            "abs_error": None,
            "rel_error": None,
            "threshold": 0.005,
            "threshold_kind": "relative",
            "supported": False,
        },
    }
    _write_complete_artifact(artifact, evidence)

    violations = _phase_2_dod_violations(evidence, evidence_root=tmp_path)
    assert violations == []
