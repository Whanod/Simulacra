"""Transaction ordering strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

import numpy as np

from defi_sim.core.types import Action, AgentId, AgentState, MarketSnapshot, Numeric

if TYPE_CHECKING:
    from defi_sim.engine.scheduler import LockedAction


def _priority_lamports(action: Action) -> Numeric:
    """CU-aware priority fee in lamports."""
    helper = getattr(action, "priority_lamports", None)
    if callable(helper):
        return helper()
    return 0


@dataclass
class OrderingContext:
    market_state: MarketSnapshot | None = None
    all_market_states: dict[str, MarketSnapshot] | None = None
    agent_states: dict[AgentId, AgentState] | None = None
    current_slot: int | None = None
    current_leader: str | None = None


class OrderingStrategy(ABC):
    @abstractmethod
    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]: ...

    def order_locked(
        self,
        actions: list["LockedAction"],
        round: int,
        context: OrderingContext | None = None,
    ) -> list["LockedAction"]:
        """LockedAction-aware overload used by Solana-side schedulers.

        PRD US-003 step 4: ``Scheduler`` returns lanes of ``LockedAction``;
        per-lane ordering applies the strategy's notion of order to a lane's
        contents. The default implementation unwraps to raw ``Action`` for
        ``order(...)``, then re-wraps via an identity map so each
        ``LockedAction``'s read/write locks survive the round-trip.
        """
        if not actions:
            return []
        action_to_locked: dict[int, "LockedAction"] = {id(la.action): la for la in actions}
        raw = [la.action for la in actions]
        ordered = self.order(raw, round, context)
        return [action_to_locked[id(a)] for a in ordered]


class FIFOOrdering(OrderingStrategy):
    """Arrival order."""
    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]:
        return actions


class RandomOrdering(OrderingStrategy):
    """Shuffle using provided RNG."""
    def __init__(self, rng: np.random.Generator | None = None):
        self._rng = rng or np.random.default_rng()

    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]:
        shuffled = list(actions)
        self._rng.shuffle(shuffled)
        return shuffled


class PriorityOrdering(OrderingStrategy):
    """Sort by Solana priority fee (lamports) descending.

    Reads ``Action.priority_lamports()``, which derives the lamport-equivalent
    fee from ``compute_unit_price_micro_lamports`` and ``compute_unit_limit``.
    """
    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]:
        return sorted(actions, key=_priority_lamports, reverse=True)


class SandwichOrdering(OrderingStrategy):
    """Brackets target agent's actions with adversarial front/back-run."""
    def __init__(self, adversarial_agent_ids: set[AgentId],
                 target_agent_ids: set[AgentId]):
        self._adversarial = adversarial_agent_ids
        self._targets = target_agent_ids

    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]:
        adversarial = [a for a in actions if a.agent_id in self._adversarial]
        targets = [a for a in actions if a.agent_id in self._targets]
        others = [a for a in actions if a.agent_id not in self._adversarial and a.agent_id not in self._targets]

        # Interleave: adversarial before each target, then target, then adversarial after
        result: list[Action] = []
        half = len(adversarial) // 2
        front_run = adversarial[:half]
        back_run = adversarial[half:]
        result.extend(front_run)
        result.extend(targets)
        result.extend(back_run)
        result.extend(others)
        return result


class BlockBuilder(OrderingStrategy):
    """Models a block builder for MEV simulation."""

    def __init__(
        self,
        builder_agent_id: AgentId,
        strategy: Callable[[list[Action], OrderingContext], list[Action]],
    ):
        self._builder_id = builder_agent_id
        self._strategy = strategy

    def order(self, actions: list[Action], round: int,
              context: OrderingContext | None = None) -> list[Action]:
        return self._strategy(actions, context or OrderingContext())
