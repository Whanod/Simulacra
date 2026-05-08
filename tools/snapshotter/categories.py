"""Stress-category enum + scoring thresholds (FIX-019, US-004).

Phase 2 targets the ``steady_state`` baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "DEFAULT_THRESHOLDS",
    "CategoryThresholds",
    "CapturePolicy",
    "StressCategory",
]


class StressCategory(str, Enum):
    """Targeted corpus stress categories."""

    STEADY_STATE = "steady_state"
    HIGH_VOLUME_DEX = "high_volume_dex"

    @classmethod
    def parse(cls, value: str) -> "StressCategory":
        """Parse ``value`` (case-insensitive, hyphen/underscore tolerant)."""
        normalized = value.strip().lower().replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(
            f"unknown stress category {value!r}; expected one of "
            + ", ".join(m.value for m in cls)
        )


@dataclass(frozen=True)
class CategoryThresholds:
    """Numeric cut-offs used by :func:`scoring.score_for_category`."""

    # Vote txs alone consume ~700/slot; the tx_count cap accommodates that
    # validator-vote baseline.
    steady_state_max_tx_count: int = 1800
    steady_state_max_total_cu: int = 80_000_000
    steady_state_max_tip_count: int = 3
    steady_state_max_decoded_swaps: int = 5


@dataclass(frozen=True)
class CapturePolicy:
    """Per-category capture policy (which programs to snapshot)."""

    programs: dict[StressCategory, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "CapturePolicy":
        """Block-only capture (no ``getProgramAccounts``)."""
        return cls(programs={category: () for category in StressCategory})


DEFAULT_THRESHOLDS = CategoryThresholds()
