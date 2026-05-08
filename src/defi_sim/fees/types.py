"""Fee model protocol and result type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from defi_sim.core.types import ExecutionContext, Numeric


@dataclass
class FeeResult:
    total_fee: Numeric = 0
    splits: dict[str, Numeric] = field(default_factory=dict)
    net_amount: Numeric = 0


class FeeModel(Protocol):
    """Protocol for fee computation functions."""
    def __call__(self, gross: Numeric, ctx: ExecutionContext) -> FeeResult: ...
