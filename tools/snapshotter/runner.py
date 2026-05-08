"""Slot-processing runner (FIX-019).

The runner is the testable orchestration layer the CLI calls into:

1. Pull the slot via :func:`defi_sim_solana.replay.slot_client.get_slot`.
2. Extract :class:`SlotSignals` and score against the targeted categories.
3. If the slot qualifies for a category that still needs a real fixture
   (per :class:`CategoryCoverage`), invoke
   :func:`tools.cache_corpus_slot.cache_slot_corpus` to write the proof
   fixture under ``solana-plans/calibration/corpus/<slot>/``.
4. Stamp the freshly-written manifest with the matching ``category:``
   value so :func:`corpus_category_coverage` picks it up on the next pass.

All dependencies are injectable so the unit-test surface never opens a
socket or hits live RPC.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

from defi_sim_solana.replay.account_client import (
    HistoricalAccountBackend,
    default_recent_backend,
)
from defi_sim_solana.replay.slot_client import (
    SlotSnapshot,
    SolanaClient,
    default_client,
    get_slot,
)

from .categories import CapturePolicy, CategoryThresholds, DEFAULT_THRESHOLDS, StressCategory
from .coverage import CategoryCoverage, corpus_category_coverage, needs_capture
from .scoring import CategoryScore, SlotSignals, extract_signals, score_for_category

__all__ = [
    "CaptureCallable",
    "SlotProcessingResult",
    "SnapshotterConfig",
    "SnapshotterRunner",
]


_LOG = logging.getLogger(__name__)


class CaptureCallable(Protocol):
    """Shape of the capture entry point.

    Default implementation is :func:`tools.cache_corpus_slot.cache_slot_corpus`;
    tests inject a fake to avoid filesystem / RPC traffic.
    """

    def __call__(
        self,
        slot: int,
        programs: list[str],
        *,
        out_dir: Path,
        slot_client: SolanaClient,
        account_backend: HistoricalAccountBackend | None = None,
        artifact_storage_uri: str | None = None,
    ) -> dict[str, Path]: ...


@dataclass(frozen=True)
class SlotProcessingResult:
    """Outcome of processing one slot.

    ``captured_categories`` lists categories the snapshotter wrote a fixture
    for; ``skipped_categories`` lists categories that qualified but had been
    deselected (e.g. already covered, deadline passed). ``errors`` records
    capture failures keyed by category — the runner does not raise on
    capture errors so a single bad slot does not crash the websocket loop.
    """

    slot: int
    signals: SlotSignals
    scores: tuple[CategoryScore, ...]
    captured_categories: tuple[StressCategory, ...] = ()
    skipped_categories: tuple[StressCategory, ...] = ()
    errors: tuple[tuple[StressCategory, str], ...] = ()
    elapsed_ms: float = 0.0
    capture_window_missed: bool = False

    def qualified_categories(self) -> tuple[StressCategory, ...]:
        return tuple(score.category for score in self.scores if score.qualifies)


@dataclass
class SnapshotterConfig:
    """Configuration for :class:`SnapshotterRunner`.

    ``capture_window_ms`` is the soft budget between receiving a slot and
    finishing the corpus write. Slots that exceed the budget are still
    written but flagged with ``capture_window_missed=True`` so monitoring
    can alert.
    """

    out_dir: Path
    # Auto-runner only scores categories whose scorers are wired in
    # ``scoring._SCORERS``. ``HIGH_VOLUME_DEX`` is captured manually by
    # the lighthouse calibration tool (see
    # ``tests/calibration/test_whirlpool_lighthouse.py``) and intentionally
    # excluded from the watch-loop default.
    target_categories: tuple[StressCategory, ...] = (StressCategory.STEADY_STATE,)
    thresholds: CategoryThresholds = DEFAULT_THRESHOLDS
    capture_policy: CapturePolicy = field(default_factory=CapturePolicy.default)
    capture_window_ms: int = 1500
    artifact_storage_uri: str | None = None
    dry_run: bool = False


class SnapshotterRunner:
    """Synchronous slot processor.

    All blocking dependencies are injected so unit tests can run without
    network access. The default constructor wires the live Helius clients;
    pass ``slot_client=`` / ``account_backend=`` / ``capture=`` to override.
    """

    def __init__(
        self,
        config: SnapshotterConfig,
        *,
        slot_client: SolanaClient | None = None,
        account_backend: HistoricalAccountBackend | None = None,
        capture: CaptureCallable | None = None,
        coverage_provider: Callable[[], CategoryCoverage] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._slot_client = slot_client
        self._account_backend = account_backend
        self._capture = capture
        self._coverage_provider = coverage_provider or (
            lambda: corpus_category_coverage(config.out_dir)
        )
        self._clock = clock

    def process_slot(self, slot_number: int) -> SlotProcessingResult:
        """Score and (if applicable) capture ``slot_number``.

        Returns a :class:`SlotProcessingResult` whether or not anything was
        captured. Capture failures are recorded in ``errors`` rather than
        raised so the websocket loop survives transient RPC issues.
        """
        start = self._clock()
        snapshot = self._fetch_slot(slot_number)
        signals = extract_signals(snapshot)
        coverage = self._coverage_provider()

        scores: list[CategoryScore] = []
        capture_target: tuple[StressCategory, CategoryScore] | None = None
        skipped: list[StressCategory] = []
        for category in self.config.target_categories:
            score = score_for_category(signals, category, self.config.thresholds)
            scores.append(score)
            if not score.qualifies:
                continue
            if not needs_capture(category, coverage):
                skipped.append(category)
                continue
            # First qualifying category that needs a fixture wins. Iterating
            # ``target_categories`` in order means more-specific categories
            # (e.g. ``token_launch``) get picked before catch-all categories
            # (``steady_state``). One slot → one capture → one category.
            if capture_target is None:
                capture_target = (category, score)
            else:
                skipped.append(category)

        captured: list[StressCategory] = []
        errors: list[tuple[StressCategory, str]] = []
        if capture_target is not None:
            category, score = capture_target
            if self.config.dry_run:
                _LOG.info(
                    "snapshotter dry-run: slot=%s category=%s reason=%s",
                    slot_number, category.value, score.reason,
                )
                captured.append(category)
            else:
                try:
                    self._invoke_capture(slot_number, category, score)
                    captured.append(category)
                except Exception as exc:  # capture failures are non-fatal
                    _LOG.exception(
                        "snapshotter capture failed: slot=%s category=%s",
                        slot_number, category.value,
                    )
                    errors.append((category, str(exc)))

        elapsed_ms = (self._clock() - start) * 1000.0
        capture_window_missed = bool(captured) and elapsed_ms > self.config.capture_window_ms
        if capture_window_missed:
            _LOG.warning(
                "snapshotter capture-window missed: slot=%s elapsed_ms=%.1f budget_ms=%s",
                slot_number, elapsed_ms, self.config.capture_window_ms,
            )

        return SlotProcessingResult(
            slot=slot_number,
            signals=signals,
            scores=tuple(scores),
            captured_categories=tuple(captured),
            skipped_categories=tuple(skipped),
            errors=tuple(errors),
            elapsed_ms=elapsed_ms,
            capture_window_missed=capture_window_missed,
        )

    def _fetch_slot(self, slot_number: int) -> SlotSnapshot:
        if self._slot_client is not None:
            return get_slot(slot_number, client=self._slot_client)
        return get_slot(slot_number, client=default_client())

    def _invoke_capture(
        self,
        slot_number: int,
        category: StressCategory,
        score: CategoryScore,
    ) -> None:
        capture = self._capture
        if capture is None:
            from tools.cache_corpus_slot import cache_slot_corpus

            capture = cache_slot_corpus
        slot_client = self._slot_client or default_client()
        account_backend = self._account_backend or default_recent_backend()
        programs = list(self.config.capture_policy.programs.get(category, ()))
        capture(
            slot_number,
            programs,
            out_dir=self.config.out_dir,
            slot_client=slot_client,
            account_backend=account_backend if programs else None,
            artifact_storage_uri=self.config.artifact_storage_uri,
        )
        snapshot = get_slot(slot_number, client=slot_client)
        self._stamp_manifest(slot_number, category, score, snapshot)

    def _stamp_manifest(
        self,
        slot_number: int,
        category: StressCategory,
        score: CategoryScore,
        snapshot: SlotSnapshot,
    ) -> None:
        """Write ``category`` and capture metadata to the slot's manifest.

        ``cache_slot_corpus`` already writes a placeholder ``manifest.yaml``;
        this rewrites it to include the snapshotter-derived category and
        capture provenance so reviewers can see *why* the slot was selected
        and ``corpus_category_coverage`` picks it up.
        """
        manifest_path = self.config.out_dir / str(slot_number) / "manifest.yaml"
        if not manifest_path.exists():
            _LOG.warning(
                "snapshotter could not stamp manifest: %s missing", manifest_path,
            )
            return
        existing = manifest_path.read_text(encoding="utf-8")
        captured_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        header = "\n".join(
            [
                f"# Captured by snapshotter (FIX-019) for category {category.value}.",
                f"# Selection reason: {score.reason}",
                f"# captured_at: {captured_at}",
                "",
            ]
        )
        body_lines: list[str] = []
        seen_category = False
        seen_captured_at = False
        for line in existing.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("# Hand-fill") or stripped.startswith("# See PRD"):
                continue
            if stripped.startswith("category:"):
                body_lines.append(f"category: {category.value}")
                seen_category = True
                continue
            if stripped.startswith("captured_at:"):
                body_lines.append(f"captured_at: {captured_at}")
                seen_captured_at = True
                continue
            body_lines.append(line)
        body_lines = _strip_leading_blank_comment_run(body_lines)
        if not seen_category:
            body_lines = _insert_after_slot(
                body_lines,
                f"category: {category.value}",
            )
        if not seen_captured_at:
            body_lines = _insert_after_slot(body_lines, f"captured_at: {captured_at}")
        manifest_path.write_text(
            header + "\n".join(body_lines).rstrip() + "\n", encoding="utf-8"
        )


def _strip_leading_blank_comment_run(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and (not out[0].strip() or out[0].lstrip().startswith("#")):
        out.pop(0)
    return out


def _insert_after_slot(lines: list[str], new_line: str) -> list[str]:
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.lstrip().startswith("slot:"):
            out.append(new_line)
            inserted = True
    if not inserted:
        out.insert(0, new_line)
    return out


def iter_results(
    runner: SnapshotterRunner, slot_numbers: Iterable[int]
) -> Iterable[SlotProcessingResult]:
    """Adapter that maps an iterable of slot numbers through ``runner``.

    Used by :mod:`websocket` to wire an async slotSubscribe stream into the
    synchronous runner without coupling the two modules.
    """
    for slot in slot_numbers:
        yield runner.process_slot(slot)
