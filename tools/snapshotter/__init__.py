"""Calibration-corpus snapshotter (FIX-019).

A tool operators run on demand against paid Helius RPC. For each processed
slot it scores against the corpus's ``steady_state`` baseline and, if the
slot qualifies and the category does not yet have a real fixture, captures
the slot via :mod:`tools.cache_corpus_slot`.

Two run modes:

* ``--once --slot <N>`` — typical authoring path.
* ``--watch`` — opportunistic loop subscribed to ``slotSubscribe``.

The package is split so the testable parts (scoring, capture orchestration)
do not pull in the websocket dependency: ``categories`` / ``scoring`` /
``coverage`` / ``runner`` are pure Python and unit-testable; ``websocket``
and ``__main__`` are the watch-mode wiring.
"""

from .categories import (
    DEFAULT_THRESHOLDS,
    CategoryThresholds,
    StressCategory,
)
from .coverage import (
    CategoryCoverage,
    corpus_category_coverage,
    needs_capture,
)
from .runner import (
    SlotProcessingResult,
    SnapshotterConfig,
    SnapshotterRunner,
)
from .scoring import (
    CategoryScore,
    SlotSignals,
    extract_signals,
    score_for_category,
)

__all__ = [
    "CategoryCoverage",
    "CategoryScore",
    "CategoryThresholds",
    "DEFAULT_THRESHOLDS",
    "SlotProcessingResult",
    "SlotSignals",
    "SnapshotterConfig",
    "SnapshotterRunner",
    "StressCategory",
    "corpus_category_coverage",
    "extract_signals",
    "needs_capture",
    "score_for_category",
]
