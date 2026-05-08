"""Tip curve specification for Jito searcher tip sizing.

PRD US-013 (line 1026): A `TipCurveSpec` describes how a `JitoSearcher` maps
expected EV (lamports) and a priority-fee quote to a tip amount. Phase 1.11
ships ``linear`` and ``percent_of_ev`` kinds; ``custom`` is reserved for a
later sub-task that will define the serializable-callable shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(kw_only=True)
class TipCurveSpec:
    kind: Literal["linear", "percent_of_ev", "custom"]
    slope_micro_lamports_per_ev: float = 0.05
    percent: float = 0.5

    def apply(self, expected_ev: int, fee_quote: int) -> int:  # noqa: ARG002
        """Map ``(expected_ev, fee_quote)`` to a tip in lamports.

        PRD US-013 line 1042: Phase 1.11 ships ``linear`` (tip = slope*EV,
        e.g. 5% of EV at the default 0.05 slope per the PRD comment) and
        ``percent_of_ev`` (tip = percent*EV). ``fee_quote`` is plumbed for
        future curves that consume the priority-fee-market reading directly;
        the Phase-1.11 curves are EV-only, so it is unused. ``custom`` is
        deferred to a later sub-task that defines the serializable-callable
        shape (PRD line 1033).
        """
        if self.kind == "linear":
            return int(round(self.slope_micro_lamports_per_ev * expected_ev))
        if self.kind == "percent_of_ev":
            return int(round(self.percent * expected_ev))
        if self.kind == "custom":
            raise NotImplementedError(
                "custom TipCurveSpec.kind is deferred per PRD line 1033"
            )
        raise ValueError(f"unknown TipCurveSpec kind: {self.kind!r}")
