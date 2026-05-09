"""Runtime path helpers for data files shipped beside the source tree."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["project_root", "solana_plans_root"]


def project_root(anchor: Path | None = None) -> Path:
    """Return the directory containing ``solana-plans``.

    Source checkouts resolve naturally from ``Path.cwd()`` or module parents.
    Installed Docker images set ``DEFI_SIM_REPO_ROOT=/app`` because package
    ``__file__`` paths point into site-packages while runtime fixtures are
    copied beside the app.
    """

    env_root = os.environ.get("DEFI_SIM_REPO_ROOT")
    candidates: list[Path] = []
    if env_root:
        candidates.append(Path(env_root))

    starts = [Path.cwd()]
    if anchor is not None:
        starts.append(anchor.resolve())
    starts.append(Path(__file__).resolve())

    seen: set[Path] = set()
    for start in starts:
        base = start if start.is_dir() else start.parent
        for candidate in (base, *base.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    candidates.append(Path("/app"))

    for candidate in candidates:
        if (candidate / "solana-plans").is_dir():
            return candidate

    return candidates[0]


def solana_plans_root(anchor: Path | None = None) -> Path:
    return project_root(anchor) / "solana-plans"
