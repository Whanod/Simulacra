"""Runtime protocol configuration.

ParameterStore — mutable key-value store for protocol parameters that can
change mid-simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from defi_sim.core.types import AgentId


@dataclass
class ScheduledChange:
    """A parameter change scheduled for a future round."""
    key: str
    value: Any
    execute_at_round: int
    proposed_by: AgentId | None = None
    proposal_id: str | None = None


class ParameterStore:
    """Mutable key-value store for protocol parameters.

    Supports immediate, scheduled, and governance-driven mutations.
    """

    def __init__(self, defaults: dict[str, Any] | None = None):
        self._params: dict[str, Any] = dict(defaults or {})
        self._pending: list[ScheduledChange] = []
        self._history: list[tuple[int, str, Any, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self._params.get(key, default)

    def set(self, key: str, value: Any, round: int = 0) -> Any:
        """Immediate parameter change. Records in history."""
        old = self._params.get(key)
        self._params[key] = value
        self._history.append((round, key, old, value))
        return old

    def schedule(self, change: ScheduledChange) -> None:
        """Queue a parameter change for a future round."""
        self._pending.append(change)

    def apply_pending(self, current_round: int) -> list[tuple[ScheduledChange, Any]]:
        """Apply all scheduled changes whose execute_at_round <= current_round."""
        applied = [c for c in self._pending if c.execute_at_round <= current_round]
        applied_with_old: list[tuple[ScheduledChange, Any]] = []
        for c in applied:
            old = self.set(c.key, c.value, current_round)
            applied_with_old.append((c, old))
        self._pending = [c for c in self._pending if c.execute_at_round > current_round]
        return applied_with_old

    def get_history(self, key: str | None = None) -> list[tuple[int, str, Any, Any]]:
        if key is None:
            return list(self._history)
        return [(r, k, o, n) for r, k, o, n in self._history if k == key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": dict(self._params),
            "pending": [
                {
                    "key": change.key,
                    "value": change.value,
                    "execute_at_round": change.execute_at_round,
                    "proposed_by": change.proposed_by,
                    "proposal_id": change.proposal_id,
                }
                for change in self._pending
            ],
            "history": list(self._history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParameterStore":
        store = cls(defaults=data.get("params", {}))
        store._pending = [
            ScheduledChange(
                key=change["key"],
                value=change["value"],
                execute_at_round=change["execute_at_round"],
                proposed_by=change.get("proposed_by"),
                proposal_id=change.get("proposal_id"),
            )
            for change in data.get("pending", [])
        ]
        store._history = [tuple(entry) for entry in data.get("history", [])]
        return store
