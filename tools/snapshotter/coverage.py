"""Corpus coverage tracker (FIX-019, US-004 Gate B).

Scans ``solana-plans/calibration/corpus/<slot>/manifest.yaml`` files and
reports which targeted stress categories already have a real (non-
synthetic) fixture committed. The runner consults this map to decide
whether a qualifying slot still needs capture.

Manifest schema:

```yaml
slot: <int>
category: steady_state | synthetic
captured_at: <ISO-8601 UTC>           # optional; written by the snapshotter
```

A fixture counts as a real calibration fixture iff its ``category`` field
parses as a :class:`StressCategory` member (synthetic / development
markers do not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from defi_sim_solana.replay.corpus import corpus_root

from .categories import StressCategory

__all__ = [
    "CategoryCoverage",
    "corpus_category_coverage",
    "needs_capture",
]


_CATEGORY_LINE_RE = re.compile(r"^\s*category\s*:\s*([A-Za-z0-9_\-]+)\s*$", re.MULTILINE)
_SLOT_LINE_RE = re.compile(r"^\s*slot\s*:\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CategoryCoverage:
    """Which slots cover each stress category."""

    by_category: dict[StressCategory, tuple[int, ...]] = field(default_factory=dict)

    def slots_for(self, category: StressCategory) -> tuple[int, ...]:
        return self.by_category.get(category, ())

    def has_real_fixture(self, category: StressCategory) -> bool:
        return bool(self.slots_for(category))

    def missing_categories(self) -> tuple[StressCategory, ...]:
        return tuple(c for c in StressCategory if not self.has_real_fixture(c))


def corpus_category_coverage(root: Path | None = None) -> CategoryCoverage:
    """Scan ``root`` (default: corpus root) and return :class:`CategoryCoverage`.

    Reads the ``category`` and ``slot`` lines out of each manifest with a
    minimal regex — keeps the loader free of a YAML dependency at import
    time. Manifests without a ``category`` field, or with ``category:
    synthetic``, are excluded; those slots are development-mode fixtures
    and do not satisfy any stress-category requirement.
    """
    base = root if root is not None else corpus_root()
    by_category: dict[StressCategory, list[int]] = {}
    if not base.is_dir():
        return CategoryCoverage(by_category={})
    for manifest_path in sorted(base.glob("*/manifest.yaml")):
        category = _read_category(manifest_path)
        if category is None:
            continue
        slot = _read_slot(manifest_path) or _slot_from_dirname(manifest_path)
        if slot is None:
            continue
        by_category.setdefault(category, []).append(slot)
    return CategoryCoverage(
        by_category={cat: tuple(sorted(slots)) for cat, slots in by_category.items()},
    )


def needs_capture(
    category: StressCategory,
    coverage: CategoryCoverage,
) -> bool:
    """Return True iff ``category`` has no real fixture committed yet."""
    return not coverage.has_real_fixture(category)


def _read_category(path: Path) -> StressCategory | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _CATEGORY_LINE_RE.search(text)
    if match is None:
        return None
    raw = match.group(1).strip().lower()
    if raw in ("synthetic", "development", "dev"):
        return None
    try:
        return StressCategory.parse(raw)
    except ValueError:
        return None


def _read_slot(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _SLOT_LINE_RE.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _slot_from_dirname(path: Path) -> int | None:
    try:
        return int(path.parent.name)
    except (TypeError, ValueError):
        return None
