"""Unit tests for ``tools.snapshotter.coverage`` (FIX-019)."""

from __future__ import annotations

from pathlib import Path

from tools.snapshotter import StressCategory
from tools.snapshotter.coverage import corpus_category_coverage, needs_capture


def _write_manifest(corpus_root: Path, slot: int, category: str) -> None:
    slot_dir = corpus_root / str(slot)
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "manifest.yaml").write_text(
        f"slot: {slot}\ncategory: {category}\nexpected: {{}}\nthresholds: {{}}\n",
        encoding="utf-8",
    )


def test_corpus_category_coverage_picks_up_real_categories(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, 200, "steady_state")
    _write_manifest(corpus, 201, "synthetic")  # development fixture

    coverage = corpus_category_coverage(root=corpus)
    assert coverage.slots_for(StressCategory.STEADY_STATE) == (200,)


def test_corpus_category_coverage_treats_synthetic_as_uncovered(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, 1, "synthetic")
    _write_manifest(corpus, 2, "development")
    coverage = corpus_category_coverage(root=corpus)
    assert all(coverage.slots_for(c) == () for c in StressCategory)
    assert sorted(c.value for c in coverage.missing_categories()) == sorted(
        c.value for c in StressCategory
    )


def test_corpus_category_coverage_handles_missing_root(tmp_path: Path) -> None:
    coverage = corpus_category_coverage(root=tmp_path / "does-not-exist")
    assert coverage.by_category == {}


def test_needs_capture_true_when_no_real_fixture_exists(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, 1, "synthetic")
    coverage = corpus_category_coverage(root=corpus)
    for category in StressCategory:
        assert needs_capture(category, coverage) is True


def test_needs_capture_false_when_real_fixture_exists(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, 200, "steady_state")
    coverage = corpus_category_coverage(root=corpus)
    assert needs_capture(StressCategory.STEADY_STATE, coverage) is False


def test_corpus_category_coverage_falls_back_to_dirname_for_slot(tmp_path: Path) -> None:
    """A manifest without an explicit slot still resolves via its directory name."""
    corpus = tmp_path / "corpus"
    slot_dir = corpus / "999"
    slot_dir.mkdir(parents=True)
    (slot_dir / "manifest.yaml").write_text(
        "category: steady_state\nexpected: {}\n", encoding="utf-8"
    )
    coverage = corpus_category_coverage(root=corpus)
    assert coverage.slots_for(StressCategory.STEADY_STATE) == (999,)


def test_corpus_category_coverage_skips_unknown_category(tmp_path: Path) -> None:
    """A manifest tagged with a future category (not yet in the enum) is
    skipped gracefully — it neither raises nor lights up coverage."""
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, 5, "token_launch")  # absent from enum today
    coverage = corpus_category_coverage(root=corpus)
    assert all(coverage.slots_for(c) == () for c in StressCategory)
