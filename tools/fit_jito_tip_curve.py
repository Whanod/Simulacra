"""Fit and persist the Jito tip-quote calibration curve (FIX-020).

Reads the latest bundle capture under
``solana-plans/calibration/corpus/jito_bundles/`` (or a directory passed
via ``--corpus``) and writes a fitted ``TipQuoteCurve`` to
``solana-plans/calibration/jito_tip_curves.yaml``.

Usage::

    python tools/fit_jito_tip_curve.py
    python tools/fit_jito_tip_curve.py --corpus solana-plans/calibration/corpus/jito_bundles/2026-05-05
    python tools/fit_jito_tip_curve.py --out custom-curve.yaml --only-landed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from defi_sim_solana.calibration import fit_tip_quote_curve
from defi_sim_solana.calibration.tip_quote import write_tip_quote_curve_yaml

# Re-import the canonical lighthouse cohort from the capture tool so the
# fitter and capture stay in lockstep on cohort definition.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.cache_jito_bundles import DEFAULT_LIGHTHOUSE_COHORT  # noqa: E402

DEFAULT_CORPUS_ROOT = Path("solana-plans/calibration/corpus/jito_bundles")
DEFAULT_OUT = Path("solana-plans/calibration/jito_tip_curves.yaml")


def _pick_latest_corpus(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        reverse=True,
    )
    for candidate in candidates:
        if (candidate / "bundles.jsonl.gz").exists():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit the Jito tip-quote calibration curve.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help=(
            "Capture directory containing bundles.jsonl.gz. Defaults to "
            "the most recent dated subdir under "
            "solana-plans/calibration/corpus/jito_bundles/."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output YAML path.",
    )
    parser.add_argument(
        "--cohort",
        type=str,
        default=",".join(DEFAULT_LIGHTHOUSE_COHORT),
        help="Comma-separated cohort pubkeys (default: lighthouse SOL/USDC).",
    )
    parser.add_argument(
        "--only-landed",
        action="store_true",
        help="Drop bundles with any reverted tx from the percentile fit.",
    )
    args = parser.parse_args(argv)

    corpus = args.corpus or _pick_latest_corpus(DEFAULT_CORPUS_ROOT)
    if corpus is None:
        parser.error(
            f"no corpus directory at {DEFAULT_CORPUS_ROOT} — run "
            "tools/cache_jito_bundles.py first or pass --corpus."
        )

    cohort = tuple(p.strip() for p in args.cohort.split(",") if p.strip())
    curve = fit_tip_quote_curve(corpus, cohort=cohort, only_landed=args.only_landed)
    write_tip_quote_curve_yaml(args.out, curve)
    print(
        f"wrote {args.out}: n_bundles={curve.n_bundles} "
        f"n_slots={curve.n_slots} n_in_cohort={curve.n_in_cohort} "
        f"landing_rate={(curve.landing_rate or 0):.4f}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
