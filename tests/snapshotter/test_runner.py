"""Unit tests for ``tools.snapshotter.runner.SnapshotterRunner`` (FIX-019)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from defi_sim_solana.replay.slot_client import SlotSnapshot
from tools.snapshotter import (
    CategoryCoverage,
    SnapshotterConfig,
    SnapshotterRunner,
    StressCategory,
)


class _FakeSlotClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[int] = []
        self.endpoint = "fake://slot"

    def get_block(self, slot: int) -> dict[str, Any]:
        self.calls.append(slot)
        return self._payload


def _quiet_block() -> dict[str, Any]:
    """getBlock-shaped payload with vote-only traffic — qualifies steady_state."""
    txs = [
        {
            "message": {
                "accountKeys": ["Fee1", "VoteProgram1111111"],
                "instructions": [{"programIdIndex": 1, "accounts": [], "data": ""}],
            },
            "meta": {"computeUnitsConsumed": 5000, "innerInstructions": []},
        }
        for _ in range(800)
    ]
    return {"slot": 100, "transactions": txs, "blockhash": "X"}


def _empty_coverage_provider():
    return CategoryCoverage(by_category={})


def _full_coverage_provider():
    return CategoryCoverage(by_category={cat: (1,) for cat in StressCategory})


def _build_runner(
    *,
    config: SnapshotterConfig,
    block: dict[str, Any],
    capture: Any | None = None,
    coverage_provider: Any | None = None,
    clock=None,
) -> tuple[SnapshotterRunner, _FakeSlotClient, list[Any]]:
    slot_client = _FakeSlotClient(block)
    capture_calls: list[Any] = []
    if capture is None:
        def _record_capture(slot, programs, **kwargs):  # noqa: ANN001
            capture_calls.append({"slot": slot, "programs": list(programs)})
            (kwargs["out_dir"] / str(slot)).mkdir(parents=True, exist_ok=True)
            (kwargs["out_dir"] / str(slot) / "manifest.yaml").write_text(
                f"slot: {slot}\nexpected:\n  tx_count: 0\nthresholds: {{}}\n",
                encoding="utf-8",
            )
            return {}
        capture = _record_capture
    runner = SnapshotterRunner(
        config,
        slot_client=slot_client,
        capture=capture,
        coverage_provider=coverage_provider or _empty_coverage_provider,
        clock=clock or (lambda: 0.0),
    )
    return runner, slot_client, capture_calls


def test_runner_captures_qualifying_steady_state_slot(tmp_path: Path) -> None:
    config = SnapshotterConfig(out_dir=tmp_path)
    runner, _, capture_calls = _build_runner(config=config, block=_quiet_block())
    result = runner.process_slot(100)
    assert result.captured_categories == (StressCategory.STEADY_STATE,)
    assert len(capture_calls) == 1


def test_runner_skips_already_covered_category(tmp_path: Path) -> None:
    """A slot that qualifies for a category already covered must not capture."""
    config = SnapshotterConfig(out_dir=tmp_path)
    runner, _, capture_calls = _build_runner(
        config=config,
        block=_quiet_block(),
        coverage_provider=_full_coverage_provider,
    )
    result = runner.process_slot(100)
    assert result.captured_categories == ()
    assert capture_calls == []


def test_runner_dry_run_records_qualification_but_skips_capture(tmp_path: Path) -> None:
    config = SnapshotterConfig(out_dir=tmp_path, dry_run=True)
    runner, _, capture_calls = _build_runner(config=config, block=_quiet_block())
    result = runner.process_slot(100)
    assert StressCategory.STEADY_STATE in result.captured_categories
    assert capture_calls == []


def test_runner_records_errors_without_raising(tmp_path: Path) -> None:
    def _failing_capture(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("RPC blew up")

    config = SnapshotterConfig(out_dir=tmp_path)
    runner, _, _ = _build_runner(
        config=config,
        block=_quiet_block(),
        capture=_failing_capture,
    )
    result = runner.process_slot(100)
    assert result.captured_categories == ()
    assert result.errors and result.errors[0][0] is StressCategory.STEADY_STATE
    assert "RPC blew up" in result.errors[0][1]


def test_runner_marks_capture_window_missed_on_slow_processing(tmp_path: Path) -> None:
    times = iter([0.0, 5.0])  # 5 seconds elapsed
    config = SnapshotterConfig(out_dir=tmp_path, capture_window_ms=1500)
    runner, _, _ = _build_runner(
        config=config,
        block=_quiet_block(),
        clock=lambda: next(times),
    )
    result = runner.process_slot(100)
    assert result.capture_window_missed is True
    assert result.elapsed_ms >= 1500


def test_runner_no_qualification_returns_empty_capture_set(tmp_path: Path) -> None:
    """A slot too busy to be steady_state captures nothing."""
    busy_block = {
        "slot": 100,
        "transactions": [
            {
                "message": {
                    "accountKeys": ["Fee1", "Anything11111111"],
                    "instructions": [
                        {"programIdIndex": 1, "accounts": [], "data": ""}
                    ],
                },
                "meta": {"computeUnitsConsumed": 50000, "innerInstructions": []},
            }
            for _ in range(5000)
        ],
        "blockhash": "X",
    }
    config = SnapshotterConfig(out_dir=tmp_path)
    runner, _, capture_calls = _build_runner(config=config, block=busy_block)
    result = runner.process_slot(100)
    assert result.captured_categories == ()
    assert capture_calls == []
    assert all(not s.qualifies for s in result.scores)


def test_runner_stamps_manifest_with_category_and_reason(tmp_path: Path) -> None:
    config = SnapshotterConfig(out_dir=tmp_path)
    runner, _, _ = _build_runner(config=config, block=_quiet_block())
    result = runner.process_slot(100)
    assert result.captured_categories == (StressCategory.STEADY_STATE,)
    manifest = (tmp_path / "100" / "manifest.yaml").read_text(encoding="utf-8")
    assert "category: steady_state" in manifest
    assert "captured_at:" in manifest
    assert "Selection reason" in manifest
    assert "tx_count=" in manifest
