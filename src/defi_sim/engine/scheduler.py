"""Scheduler seam for slot-coordinated execution.

US-003 (was 1.3) widens the scheduler contract: ``Scheduler.schedule``
takes a ``Sequence[LockedAction]`` and returns ``list[ParallelLane]``.
``SerialScheduler`` preserves today's behaviour (a single lane in input
order) without synthesizing empty locks — lock resolution is the
engine/market boundary's responsibility (PRD US-003 steps 3, 6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from defi_sim.core.types import Action, DEFAULT_CU_LIMIT_FALLBACK

# Solana writable/readable account identifier. Concrete account ids are
# provided by per-market lock resolvers (see PRD US-003 step 3).
AccountId = str


@dataclass(frozen=True)
class LockedAction:
    """An ``Action`` augmented with the read/write account locks it needs.

    Locks are produced by per-market ``LockResolver`` implementations
    before the action enters the scheduler. The conflict graph used by
    ``PriorityScheduler`` is built from these sets.
    """

    action: Action
    read_locks: frozenset[AccountId] = field(default_factory=frozenset)
    write_locks: frozenset[AccountId] = field(default_factory=frozenset)


@dataclass
class ParallelLane:
    """A connected component of the conflict graph.

    Actions inside a lane execute serially in ``actions`` order; lanes
    themselves are executed in arbitrary inter-lane order under the
    parallel-execution contract.
    """

    actions: list[LockedAction] = field(default_factory=list)


def conflicts(a: LockedAction, b: LockedAction) -> bool:
    """Pure conflict-graph predicate over two ``LockedAction``s.

    Two actions conflict if either's write set overlaps the other's
    read-or-write set. Read-read overlaps do NOT conflict (they are the
    whole point of having read locks). Pure function — does not consult
    engine state. Used by ``PriorityScheduler`` to build the conflict
    graph whose connected components become ``ParallelLane``s.
    """
    if a.write_locks & (b.read_locks | b.write_locks):
        return True
    if b.write_locks & (a.read_locks | a.write_locks):
        return True
    return False


@runtime_checkable
class LockResolver(Protocol):
    """Per-market contract that maps a raw ``Action`` to a ``LockedAction``.

    Every market or execution route that can execute an action MUST
    implement ``resolve_locks``. Lock resolution is mandatory before the
    scheduler ever sees an action — an action whose market lacks a
    resolver is rejected at admission with ``missing_lock_resolver``
    rather than silently falling back to serial execution with empty
    locks (PRD US-003 step 3).
    """

    def resolve_locks(self, action: Action, state: Any = None) -> "LockedAction": ...


class Scheduler(ABC):
    @abstractmethod
    def schedule(
        self,
        actions: Sequence[LockedAction],
        slot: int,
        state: Any = None,
    ) -> list[ParallelLane]:
        """Return a list of execution lanes.

        Within a lane, ``actions`` order is preserved. Across lanes,
        order is left to the model's executor.
        """
        ...


_SIGNATURE_COST_CU: int = 720
_WRITE_LOCK_COST_CU: int = 300


def scheduler_priority_score(locked: LockedAction) -> float:
    """Solana-shaped scheduler priority: validator reward / estimated cost.

    Mirrors agave's prio-graph scheduler shape — rank actions by
    reward-per-cost so the most fee-dense transactions land first within
    their lane. Reward is the validator's revenue (priority fee + the
    non-burned half of the base fee) per ``Action.validator_reward_lamports``.
    A v1 cost estimator combines signature cost, write-lock cost, and the
    requested compute-unit limit; instruction-data and loaded-account-data
    costs are zero in v1 and left for calibration.

    Reference: agave banking_stage prio-graph scheduler.
    """
    action = locked.action
    reward = action.validator_reward_lamports()
    cu_limit = (
        action.compute_unit_limit
        if action.compute_unit_limit is not None
        else DEFAULT_CU_LIMIT_FALLBACK
    )
    cost = (
        _SIGNATURE_COST_CU * (action.num_required_signatures or 1)
        + _WRITE_LOCK_COST_CU * len(locked.write_locks)
        + max(cu_limit, 0)
    )
    if cost <= 0:
        return 0.0
    return reward / cost


class PriorityScheduler(Scheduler):
    """Solana-shaped parallel scheduler driven by lock-conflict components.

    Builds a conflict graph (nodes = actions, edges = pairs satisfying
    ``conflicts(a, b)``) and emits one ``ParallelLane`` per connected
    component. Within a lane actions are sorted by
    ``scheduler_priority_score`` descending — explicitly NOT a global sort,
    which would be a known-wrong bug per PRD US-003.

    Reference: agave banking_stage prio-graph scheduler.
    """

    def schedule(
        self,
        actions: Sequence[LockedAction],
        slot: int,
        state: Any = None,
    ) -> list[ParallelLane]:
        n = len(actions)
        if n == 0:
            return []

        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i in range(n):
            for j in range(i + 1, n):
                if conflicts(actions[i], actions[j]):
                    union(i, j)

        components: dict[int, list[LockedAction]] = {}
        for i in range(n):
            components.setdefault(find(i), []).append(actions[i])

        lanes: list[ParallelLane] = []
        for comp in components.values():
            comp.sort(key=scheduler_priority_score, reverse=True)
            lanes.append(ParallelLane(actions=comp))
        return lanes


class SerialScheduler(Scheduler):
    """Single-lane scheduler. Preserves input order verbatim.

    Lock resolution is mandatory before the scheduler runs — this class
    deliberately does not wrap raw ``Action`` objects with empty locks.
    Tests that bypass markets must construct ``LockedAction`` instances
    explicitly at the test boundary (PRD US-003 step 2).
    """

    def schedule(
        self,
        actions: Sequence[LockedAction],
        slot: int,
        state: Any = None,
    ) -> list[ParallelLane]:
        return [ParallelLane(actions=list(actions))]


def serialize_scheduler(scheduler: Scheduler) -> dict[str, Any]:
    if isinstance(scheduler, PriorityScheduler):
        return {"type": "priority"}
    if isinstance(scheduler, SerialScheduler):
        return {"type": "serial"}
    raise TypeError(f"scheduler {type(scheduler).__name__} is not snapshot-serializable")


def deserialize_scheduler(data: dict[str, Any] | None) -> Scheduler:
    if data is None:
        return SerialScheduler()
    sched_type = data.get("type", "serial")
    if sched_type == "serial":
        return SerialScheduler()
    if sched_type == "priority":
        return PriorityScheduler()
    raise ValueError(f"unknown scheduler type: {sched_type}")
