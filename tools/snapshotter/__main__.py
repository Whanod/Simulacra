"""Snapshotter CLI entrypoint (FIX-019).

Runs the snapshotter in one of three modes:

* ``--once`` — process the single specified slot (``--slot``) and exit.
* ``--watch`` — websocket loop subscribed to ``slotSubscribe``.
* ``--dry-run`` — score-only. Never writes fixtures.

Examples::

    python -m tools.snapshotter --once --slot 280123456 \
        --out solana-plans/calibration/corpus/

    python -m tools.snapshotter --watch \
        --out solana-plans/calibration/corpus/

    python -m tools.snapshotter --watch --dry-run --max-slots 50
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .categories import StressCategory
from .runner import SnapshotterConfig, SnapshotterRunner

__all__ = ["main"]


_LOG = logging.getLogger("snapshotter")


def _parse_categories(raw: str | None) -> tuple[StressCategory, ...]:
    if not raw:
        return tuple(StressCategory)
    return tuple(StressCategory.parse(item) for item in raw.split(",") if item.strip())


def _build_runner(args: argparse.Namespace) -> SnapshotterRunner:
    config = SnapshotterConfig(
        out_dir=args.out,
        target_categories=_parse_categories(args.categories),
        capture_window_ms=args.capture_window_ms,
        artifact_storage_uri=args.artifact_storage_uri,
        dry_run=args.dry_run,
    )
    return SnapshotterRunner(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.snapshotter", description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--watch",
        action="store_true",
        help="Subscribe to slotSubscribe and process every new slot (default).",
    )
    mode.add_argument(
        "--once",
        action="store_true",
        help="Process exactly one slot (use --slot) and exit.",
    )
    parser.add_argument(
        "--slot", type=int, default=None, help="Slot number to process in --once mode."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("solana-plans/calibration/corpus/"),
        help="Corpus root directory.",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default=None,
        help="Comma-separated stress categories to target (default: all six).",
    )
    parser.add_argument(
        "--max-slots",
        type=int,
        default=None,
        help="In --watch mode, exit after processing this many slots.",
    )
    parser.add_argument(
        "--capture-window-ms",
        type=int,
        default=1500,
        help="Soft budget per slot in ms; warn when exceeded (default: 1500).",
    )
    parser.add_argument(
        "--artifact-storage-uri",
        type=str,
        default=os.environ.get("ARTIFACT_STORAGE_URI"),
        help="Path/URI for raw RPC payload artifact storage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score every slot but skip captures (testing / observability).",
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase log verbosity."
    )
    args = parser.parse_args(argv)

    log_level = max(logging.DEBUG, logging.WARNING - args.verbose * 10)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.once and args.slot is None:
        parser.error("--once requires --slot")
    if not args.once and not args.watch:
        args.watch = True

    runner = _build_runner(args)

    if args.once:
        result = runner.process_slot(args.slot)
        _log_result(result)
        return 0

    from .websocket import iter_subscribed_slots

    processed = 0
    try:
        for slot in iter_subscribed_slots(max_slots=args.max_slots):
            result = runner.process_slot(slot)
            _log_result(result)
            processed += 1
    except KeyboardInterrupt:
        _LOG.info("snapshotter received SIGINT; shutting down (%d slots processed)", processed)
    return 0


def _log_result(result) -> None:
    qualified = [score.category.value for score in result.scores if score.qualifies]
    captured = [c.value for c in result.captured_categories]
    skipped = [c.value for c in result.skipped_categories]
    if result.errors:
        for category, message in result.errors:
            _LOG.error(
                "snapshotter capture-error: slot=%s category=%s msg=%s",
                result.slot, category.value, message,
            )
    if captured:
        _LOG.info(
            "snapshotter captured: slot=%s categories=%s elapsed_ms=%.1f window_missed=%s",
            result.slot, ",".join(captured), result.elapsed_ms, result.capture_window_missed,
        )
    elif qualified:
        _LOG.debug(
            "snapshotter qualified-only: slot=%s qualified=%s skipped=%s elapsed_ms=%.1f",
            result.slot, ",".join(qualified), ",".join(skipped), result.elapsed_ms,
        )
    else:
        _LOG.debug(
            "snapshotter no-match: slot=%s tx_count=%s elapsed_ms=%.1f",
            result.slot, result.signals.tx_count, result.elapsed_ms,
        )


if __name__ == "__main__":
    sys.exit(main())
