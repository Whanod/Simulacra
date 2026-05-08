"""Slot-coordinated execution primitives.

Phase 1.0 introduces a slot-aware seam on ``ExecutionModel`` that downstream
Solana stories (1.2a / 1.3 / 1.6 / 1.7 / 1.12) plug their semantics into.

Assumptions documented here:

- **1:1 round-to-slot mapping.** ``SlotContext.slot`` equals the engine's
  current round number. Sub-round slots only become necessary if 1.12's
  reorg buffer is sized in sub-slots; today it is sized in slots, so the
  mapping holds. Any future story that breaks the 1:1 mapping must revisit
  ``SimulationEngine._execute_round``.

- **World-mode ``MultiMarketAction`` routing stays in the engine.** The
  slot pipeline operates on the flattened action list and the engine does
  the per-market dispatch inside the executor callback. 1.7 (bundle
  auction) reopens this only if a bundle's atomic boundary spans markets.

- **Phase-bucket preservation (single call shape).** ``execute_slot`` is
  invoked **once per round** with the union of non-liquidation actions
  (trading + LP). Internally the model runs admit → order on the union
  (matching legacy ``_execute_round``), splits into trading vs. LP using
  ``is_lp_action``, executes trading, invokes the engine-supplied
  ``run_liquidations`` callback, then executes LP, then ``on_slot_end``
  fires once. This preserves bit-identical behaviour for cost models that
  evolve in ``on_slot_end`` (e.g. EIP1559 base-fee updates) and for any
  admission policy that needs to see the full slot-wide action set
  (1.2a's per-slot CU budgeting depends on this).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, TYPE_CHECKING

from defi_sim.core.types import (
    Action,
    LPAction,
    MultiMarketAction,
    Numeric,
    SlotEvent,
    SlotSkippedEvent,
    TokenId,
)
from defi_sim.engine.events import Event
from defi_sim.engine.ordering import OrderingContext

if TYPE_CHECKING:
    from defi_sim.engine.bundle import Bundle, TipPayment
    from defi_sim.engine.scheduler import LockedAction


@dataclass
class ExecutedAction:
    action: Action
    execution_cost: Numeric
    cost_token: TokenId | None
    succeeded: bool
    failure_reason: str | None = None


@dataclass
class BundleExecutionResult:
    """Per-bundle outcome returned by ``SlotContext.execute_bundle``.

    PRD US-011 line 840 wires the bundle pre-stage in
    ``SolanaLikeExecution.execute_slot``. The engine-supplied
    ``execute_bundle`` callback runs the bundle's inner txs under the
    rollback boundary established by ``SimulationEngine._execute_bundle_atomically``
    (PRD US-005 line 424) and returns this result so the slot pipeline can
    fold per-tx ``ExecutedAction``s into ``SlotOutcome.executed`` and
    record revert telemetry.
    """

    reverted: bool
    failed_at_index: int | None
    failed_reason: str | None
    executed: list[ExecutedAction] = field(default_factory=list)
    # PRD US-011 line 867: tip-position semantics. Tips that actually credit
    # their recipient given the bundle's revert state, computed via
    # ``Bundle.paid_tip_payments``. Empty when the bundle reverts (atomic
    # rollback undoes any earlier tip transfer; tips at or after the failing
    # position never executed). Consumed by the upcoming ``BundleTipPaid``
    # event emission (PRD line 839).
    paid_tips: list["TipPayment"] = field(default_factory=list)


@dataclass
class SlotOutcome:
    slot: int
    admitted: list[Action]
    dropped: list[tuple[Action, str]]
    deferred: list[Action]
    executed: list[ExecutedAction]
    events: list[Event] = field(default_factory=list)


class ActionExecutor(Protocol):
    """Engine-supplied callback that runs one Action's protocol-side effects."""

    def __call__(self, action: Action, slot: int) -> ExecutedAction: ...


LockResolverFn = Callable[[Action], "LockedAction | None"]
BundleExecutorFn = Callable[["Bundle", int], BundleExecutionResult]


@dataclass
class SlotContext:
    slot: int
    pending_actions: list[Action]
    ordering_context: OrderingContext
    executor: ActionExecutor
    emit: Callable[[Event], None]
    run_liquidations: Callable[[], None] = lambda: None
    slot_event: SlotEvent | SlotSkippedEvent | None = None
    # PRD US-003 step 4: lock resolution must run before the scheduler.
    # ``SimulationEngine`` supplies a callback that consults each action's
    # market for a ``LockResolver``. ``None`` means tests that bypass the
    # engine fall back to empty-lock ``LockedAction`` wrapping at the
    # execute_slot boundary (legacy fixtures).
    resolve_locks: LockResolverFn | None = None
    # PRD US-011 line 840: bundle pre-stage callback. The engine wires this
    # to ``SimulationEngine._execute_bundle_atomically`` so a selected bundle
    # runs under the rollback boundary defined in US-005. ``None`` means the
    # bundle pre-stage is skipped entirely (legacy fixtures + non-Solana
    # callers).
    execute_bundle: BundleExecutorFn | None = None


def is_lp_action(action: Action) -> bool:
    """Return True for LPAction or a MultiMarketAction wrapping one."""
    target = action.inner if isinstance(action, MultiMarketAction) else action
    return isinstance(target, LPAction)
