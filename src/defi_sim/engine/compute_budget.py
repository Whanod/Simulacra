"""Solana compute-budget caps.

Caps are governance-mutable; treat as parameters, not constants.
Historical presets must cite their Solana feature / proposal source so
calibration regressions can pin the activation context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class ComputeBudgetSource:
    """Metadata pinning a historical preset to its activation context."""

    activation_slot: int
    reference: str  # SIMD / proposal id, feature pubkey, or governance URL


@dataclass(frozen=True)
class ComputeBudget:
    per_slot: int = 60_000_000
    per_tx: int = 1_400_000
    per_writable_account: int = 12_000_000
    source: ComputeBudgetSource | None = None

    _PRESETS: ClassVar[dict[str, "ComputeBudget"]] = {}

    @classmethod
    def preset(cls, version: str) -> "ComputeBudget":
        if version == "current":
            return cls()
        try:
            preset = cls._PRESETS[version]
        except KeyError as exc:
            raise ValueError(
                f"Unknown ComputeBudget preset {version!r}. "
                f"Known presets: {sorted({'current', *cls._PRESETS.keys()})}."
            ) from exc
        if preset.source is None:
            raise ValueError(
                f"Preset {version!r} is missing source metadata. "
                "Non-current presets must cite an activation slot and reference."
            )
        return preset

    @classmethod
    def register_preset(cls, version: str, budget: "ComputeBudget") -> None:
        if version == "current":
            raise ValueError("'current' is reserved and built from defaults.")
        if budget.source is None:
            raise ValueError(
                f"Cannot register preset {version!r} without source metadata."
            )
        cls._PRESETS[version] = budget
