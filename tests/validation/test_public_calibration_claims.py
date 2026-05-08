from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

PUBLIC_SURFACES = (
    "docs",
    "frontend/src/app",
    "frontend/src/components",
    "frontend/src/lib/services",
    "solana-plans/api-specs",
    "solana-plans/calibration",
    "src/defi_sim_api",
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".json",
    ".md",
    ".py",
    ".tsx",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}

ALLOWED_GATED_CONTEXT = (
    "absence means",
    "before the product can make",
    "cannot make",
    "do not claim",
    "does not prove",
    "must not",
    "no public",
    "not calibrated",
    "not yet",
    "synthetic",
    "until",
    "uncalibrated",
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _public_text_files() -> list[Path]:
    files: list[Path] = []
    for root in PUBLIC_SURFACES:
        surface = REPO_ROOT / root
        if not surface.exists():
            continue
        if surface.is_file():
            candidates = [surface]
        else:
            candidates = [path for path in surface.rglob("*") if path.is_file()]
        files.extend(path for path in candidates if path.suffix in TEXT_SUFFIXES)
    return sorted(files)


def test_public_surfaces_do_not_make_ungated_mainnet_calibrated_claims() -> None:
    """US-004 public-claim gate.

    The committed corpus is still synthetic development data. Public-facing UI,
    API docs, and user docs may explain that mainnet-calibrated claims are not
    yet allowed, but they must not present the product as mainnet-calibrated.
    """

    claim_pattern = re.compile(r"mainnet[\s-]+calibrated", re.IGNORECASE)
    offenders: list[str] = []

    for path in _public_text_files():
        text = path.read_text(encoding="utf-8")
        for match in claim_pattern.finditer(text):
            start = max(0, match.start() - 180)
            end = min(len(text), match.end() + 180)
            context = text[start:end].lower()
            if any(marker in context for marker in ALLOWED_GATED_CONTEXT):
                continue
            line_no = _line_number(text, match.start())
            line = text.splitlines()[line_no - 1].strip()
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {line}")

    assert not offenders, (
        "Found ungated public mainnet-calibrated claims while US-004 remains "
        "synthetic/blocked:\n" + "\n".join(offenders)
    )
