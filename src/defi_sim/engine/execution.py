"""Execution-layer abstractions.

Execution models own queue visibility, admission, ordering, transaction costs,
and refund semantics. Markets remain protocol-focused.
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Callable, Mapping

import numpy as np

from defi_sim.core.agent import Agent
from defi_sim.core.market import deserialize_callable_ref, serialize_callable_ref
from defi_sim.core.types import (
    Action,
    BlockhashExpiredEvent,
    ComputeBudgetExhaustedEvent,
    DEFAULT_CU_LIMIT_FALLBACK,
    ForkReorgEvent,
    Numeric,
    PriorityFeeMarketUpdatedEvent,
    SlotSkippedEvent,
    TokenId,
)
from defi_sim.engine.blockhash import BlockhashHistory
from defi_sim.engine.fork import ChainReorgForkSpec
from defi_sim.engine.bundle import Bundle
from defi_sim.engine.bundle_auction import BundleAuction, BundleCandidate
from defi_sim.engine.compute_budget import ComputeBudget
from defi_sim.engine.priority_fee_market import PriorityFeeMarket
from defi_sim.engine.submission_priors import SubmissionPathPriors
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.gas import (
    ComputeUnitCost,
    EIP1559Cost,
    FixedCost,
    TransactionCostModel,
    TypedCost,
    ZeroCost,
)
from defi_sim.engine.leader_schedule import LeaderSchedule
from defi_sim.engine.ordering import (
    BlockBuilder,
    FIFOOrdering,
    OrderingContext,
    OrderingStrategy,
    PriorityOrdering,
    RandomOrdering,
    SandwichOrdering,
)
from defi_sim.engine.scheduler import (
    AccountId,
    LockedAction,
    PriorityScheduler,
    Scheduler,
    SerialScheduler,
    deserialize_scheduler,
    serialize_scheduler,
)
from defi_sim.engine.slot import (
    BundleExecutionResult,
    ExecutedAction,
    SlotContext,
    SlotOutcome,
    is_lp_action,
)
from defi_sim.engine.transactions import (
    MAX_TX_SIZE_BYTES,
    AddressLookupTable,
    AltId,
    VersionedTransaction,
    compute_tx_size,
)


_LOGGER = logging.getLogger(__name__)

# Threshold for warning that a deferred action keeps getting re-queued without
# ever fitting the per-slot CU budget. Picked low enough to surface a stuck
# action quickly in tests / live runs, but high enough to ignore transient
# congestion. Removed once PRD 1.12 wires recent_blockhash expiry as the real
# eviction path.
_DEFER_WARNING_THRESHOLD = 10


def _percentiles_shifted_more_than(
    prior: dict[int, int] | None,
    current: dict[int, int],
    threshold: float,
) -> bool:
    """Return True if any percentile in ``current`` differs from ``prior`` by a
    relative magnitude greater than ``threshold`` (PRD US-010 line 745). A
    ``None`` prior — no observations yet — is always treated as a change so
    the first observation for an account emits an event."""
    if prior is None:
        return True
    for p, new_value in current.items():
        old_value = prior.get(p, 0)
        if old_value == 0:
            if new_value != 0:
                return True
            continue
        if abs(new_value - old_value) / abs(old_value) > threshold:
            return True
    return False


AdmissionResult = tuple[list[Action], list[tuple[Action, str]]]
QueueVisibilityFn = Callable[[Agent, list[Action], int, OrderingContext | None], list[Action] | None]
AdmissionPolicyFn = Callable[[list[Action], int, OrderingContext | None], AdmissionResult]


class DropReason:
    """Canonical drop-reason vocabulary surfaced via the BatchExecution
    admission-result contract. Reasons emitted into ``AdmissionResult`` and
    ``SlotOutcome.dropped`` are free-form strings, but consumers SHOULD use
    these constants so the vocabulary stays consistent across the engine,
    snapshots, and downstream telemetry.

    Solana compute-budget reasons (US-002 / US-002b):
      - ``CU_PER_TX_EXCEEDED``: action's ``compute_unit_limit`` exceeds the
        per-tx CU cap (admit-time rejection).
      - ``CU_PER_SLOT_EXCEEDED``: a deferred action could not be re-queued
        before exhausting retries / blockhash expiry, so the slot-wide CU
        budget rejected it definitively.
      - ``CU_PER_ACCOUNT_EXCEEDED`` is reserved for US-002b once
        ``LockedAction.write_locks`` lands.
      - ``MISSING_LOCK_RESOLVER``: an executable action's market does not
        implement the ``LockResolver`` contract (PRD US-003 step 3). PRD
        line 270 phrases this as "fails admission". Implementation note:
        per-action lock resolvers are supplied to ``execute_slot`` via the
        ``SlotContext.resolve_locks`` callable, so the rejection physically
        runs in the lock-resolution stage of ``execute_slot`` (after
        ``admit`` returns) rather than inside ``admit()`` itself. The
        canonical drop reason still lands in ``SlotOutcome.dropped`` and
        flows through the same ``ACTION_DROPPED`` event channel as
        admit-time rejections, so the externally observable behaviour
        matches the PRD wording — only the call-stack location differs.

    Solana submission-path reasons (US-004):
      - ``SUBMISSION_PATH_DROP``: a Bernoulli sample against the configured
        path landing prior failed for this action.
      - ``INVALID_SUBMISSION_PATH``: action targets the ``jito_relayer``
        path but is not a bundle, so the path is structurally invalid.

    Solana versioned-transaction reasons (US-009):
      - ``TX_SIZE_EXCEEDED``: the wire-format size of a ``VersionedTransaction``
        exceeds Solana's 1232-byte packet cap (``MAX_TX_SIZE_BYTES``).

    Solana blockhash-expiry reasons (US-014):
      - ``BLOCKHASH_EXPIRED``: the action's ``recent_blockhash`` is older
        than the engine's rolling blockhash-validity window (default 150
        slots). Emitted at admit-time when a ``BlockhashHistory`` is
        attached to the execution model.
    """

    CU_PER_TX_EXCEEDED = "cu_per_tx_exceeded"
    CU_PER_SLOT_EXCEEDED = "cu_per_slot_exceeded"
    CU_PER_ACCOUNT_EXCEEDED = "cu_per_account_exceeded"
    MISSING_LOCK_RESOLVER = "missing_lock_resolver"
    SUBMISSION_PATH_DROP = "submission_path_drop"
    INVALID_SUBMISSION_PATH = "invalid_submission_path"
    TX_SIZE_EXCEEDED = "tx_size_exceeded"
    BLOCKHASH_EXPIRED = "blockhash_expired"


KNOWN_DROP_REASONS: frozenset[str] = frozenset(
    {
        DropReason.CU_PER_TX_EXCEEDED,
        DropReason.CU_PER_SLOT_EXCEEDED,
        DropReason.CU_PER_ACCOUNT_EXCEEDED,
        DropReason.MISSING_LOCK_RESOLVER,
        DropReason.SUBMISSION_PATH_DROP,
        DropReason.INVALID_SUBMISSION_PATH,
        DropReason.TX_SIZE_EXCEEDED,
        DropReason.BLOCKHASH_EXPIRED,
    }
)


class ExecutionModel(ABC):
    """Owns network / scheduler semantics, not protocol semantics."""

    @abstractmethod
    def pending_actions_for_agent(
        self,
        agent: Agent,
        pending: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action] | None:
        """Return the queue view exposed to an agent before decide()."""
        ...

    @abstractmethod
    def admit(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> AdmissionResult:
        """Return (admitted_actions, dropped_actions_with_reason)."""
        ...

    @abstractmethod
    def order(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action]:
        """Build the final execution order."""
        ...

    @abstractmethod
    def cost(self, action: Action, round: int) -> Numeric:
        """Return the execution-layer cost for this action."""
        ...

    @abstractmethod
    def cost_token(self, action: Action) -> TokenId | None:
        """Return the token used to pay the execution-layer cost, if any."""
        ...

    def refund_on_failure(self, action: Action) -> bool:
        """Whether failed protocol execution refunds the execution-layer cost."""
        return False

    def on_round_end(self, num_actions: int, round: int) -> None:
        """Hook for cost models that evolve over time."""
        return None

    def supports_slot_execution(self) -> bool:
        """If True, SimulationEngine calls execute_slot() instead of admit/order/on_round_end."""
        return False

    def execute_slot(self, ctx: SlotContext) -> SlotOutcome:
        raise NotImplementedError(
            "ExecutionModel.execute_slot() called on a model that did not opt in via supports_slot_execution()"
        )

    def on_slot_end(self, outcome: SlotOutcome) -> None:
        """Slot-aware analogue of on_round_end. Called by the model from inside execute_slot()."""
        return None

    @property
    def leader_schedule(self) -> LeaderSchedule | None:
        return None

    def current_slot(self) -> int | None:
        """Most recent slot observed by the model, or None if not slot-aware."""
        return None

    def current_leader(self, slot: int) -> str | None:
        """Leader pubkey for ``slot``; None if no leader schedule is attached."""
        schedule = self.leader_schedule
        if schedule is None:
            return None
        return schedule.leader_for_slot(slot)


class DirectExecution(ExecutionModel):
    """Network-neutral default execution model."""

    def __init__(
        self,
        ordering: OrderingStrategy | None = None,
        cost_model: TransactionCostModel | None = None,
        cost_token: TokenId = "COLLATERAL",
        expose_pending_actions: bool = False,
        refund_failed_costs: bool = False,
    ):
        self._ordering = ordering or FIFOOrdering()
        self._cost_model = cost_model or ZeroCost()
        self._cost_token = cost_token
        self._expose_pending = expose_pending_actions
        self._refund_failed_costs = refund_failed_costs

    def pending_actions_for_agent(
        self,
        agent: Agent,
        pending: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action] | None:
        if not self._expose_pending:
            return None
        return list(pending)

    def admit(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> AdmissionResult:
        return list(actions), []

    def order(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action]:
        return self._ordering.order(list(actions), round, context)

    def cost(self, action: Action, round: int) -> Numeric:
        return self._cost_model.cost(action, round)

    def cost_token(self, action: Action) -> TokenId | None:
        return self._cost_token

    def refund_on_failure(self, action: Action) -> bool:
        return self._refund_failed_costs

    def on_round_end(self, num_actions: int, round: int) -> None:
        updater = getattr(self._cost_model, "update_base_fee", None)
        if callable(updater):
            updater(num_actions)


class BatchExecution(DirectExecution):
    """Composable execution model for chain-like environments."""

    def __init__(
        self,
        ordering: OrderingStrategy | None = None,
        cost_model: TransactionCostModel | None = None,
        cost_token: TokenId = "COLLATERAL",
        queue_visibility: QueueVisibilityFn | None = None,
        admission_policy: AdmissionPolicyFn | None = None,
        refund_failed_costs: bool = False,
        leader_schedule: LeaderSchedule | None = None,
        scheduler: Scheduler | None = None,
    ):
        super().__init__(
            ordering=ordering,
            cost_model=cost_model,
            cost_token=cost_token,
            expose_pending_actions=False,
            refund_failed_costs=refund_failed_costs,
        )
        self._queue_visibility = queue_visibility
        self._admission_policy = admission_policy
        self._leader_schedule = leader_schedule
        self._current_slot: int | None = None
        # PRD US-003 line 320: BatchExecution defaults to SerialScheduler
        # so chain-neutral consumers get deterministic input-order
        # execution as a building-block primitive. Subclasses targeting
        # parallel runtimes (SolanaLikeExecution) override this default.
        self._scheduler: Scheduler = scheduler or SerialScheduler()

    @property
    def leader_schedule(self) -> LeaderSchedule | None:
        return self._leader_schedule

    def current_slot(self) -> int | None:
        return self._current_slot

    def pending_actions_for_agent(
        self,
        agent: Agent,
        pending: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action] | None:
        if self._queue_visibility is None:
            return None
        visible = self._queue_visibility(agent, list(pending), round, context)
        if visible is None:
            return None
        return list(visible)

    def admit(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> AdmissionResult:
        if self._admission_policy is None:
            return list(actions), []
        admitted, dropped = self._admission_policy(list(actions), round, context)
        return list(admitted), list(dropped)


class SolanaLikeExecution(BatchExecution):
    """Solana-like preset: non-public queue by default, priority-aware ordering."""

    def __init__(
        self,
        cost_model: TransactionCostModel | None = None,
        cost_token: TokenId = "COLLATERAL",
        ordering: OrderingStrategy | None = None,
        visible_roles: set[str] | None = None,
        scheduler: Scheduler | None = None,
        leader_schedule: LeaderSchedule | None = None,
        compute_budget: ComputeBudget | None = None,
        submission_priors: SubmissionPathPriors | None = None,
        submission_rng: np.random.Generator | None = None,
        priority_fee_market: PriorityFeeMarket | None = None,
        bundle_auction: BundleAuction | None = None,
        blockhash_history: BlockhashHistory | None = None,
        fork_spec: ChainReorgForkSpec | None = None,
    ):
        role_set = set(visible_roles or set())
        self._visible_roles = set(role_set)

        def queue_visibility(
            agent: Agent,
            pending: list[Action],
            round: int,
            context: OrderingContext | None = None,
        ) -> list[Action] | None:
            if not role_set:
                return None
            if agent.state.role.name in role_set:
                return pending
            return None

        super().__init__(
            ordering=ordering or PriorityOrdering(),
            cost_model=cost_model or ComputeUnitCost(),
            cost_token=cost_token,
            queue_visibility=queue_visibility if role_set else None,
            refund_failed_costs=False,
            leader_schedule=leader_schedule,
        )
        # PRD US-003 step 4: default to the parallel scheduler. Lock
        # resolution runs upstream (``SlotContext.resolve_locks``) so
        # ``PriorityScheduler`` builds its conflict graph from real
        # per-market account locks instead of empty sets.
        self._scheduler: Scheduler = scheduler or PriorityScheduler()
        self._compute_budget: ComputeBudget = compute_budget or ComputeBudget()
        self._submission_priors: SubmissionPathPriors | None = submission_priors
        # Determinism: when running under SimulationEngine, this is overwritten
        # via ``attach_submission_rng`` with a child seed of ``SimulationSpec.seed``
        # so the per-slot Bernoulli sample is reproducible across runs (PRD
        # US-004 line 366). Standalone construction without a seed gets a
        # default-seeded RNG so tests not going through the engine still work.
        self._submission_rng: np.random.Generator = (
            submission_rng if submission_rng is not None else np.random.default_rng()
        )
        self._slot_cu_used: int = 0
        # PRD US-009 line 675: ALT registry consulted by ``compute_tx_size``
        # during the admit-time size check. Stays empty until the spec-level
        # seeder lands at PRD line 678; the gate still works for the no-ALT
        # case (every account costs 32 bytes), which is what the
        # 30-account-no-ALT exceedance test (PRD line 683) exercises.
        self._alts: dict[AltId, AddressLookupTable] = {}
        # PRD US-008 line 613: per-writable-account CU tally, reset each slot.
        # Populated by the per-account check (PRD line 614) at the
        # ``# TODO 1.2b`` site below; kept on the model so tests can introspect
        # the running tally between admit and execute.
        self._account_cu_tally: dict[AccountId, int] = {}
        # id(action) -> consecutive defer count, used to warn when an action's
        # CU footprint never fits the per-slot budget. Until PRD 1.12 lands
        # recent_blockhash expiry the engine has no other way to evict such an
        # action from the queue, so we surface the situation via logging.
        self._defer_counts: dict[int, int] = {}
        # PRD US-010 line 738: per-account priority fee market updated from each
        # admitted locked action's write-lock set. Read-locks are observational
        # only and do not move the market (PRD line 743).
        self._priority_fee_market: PriorityFeeMarket = (
            priority_fee_market if priority_fee_market is not None else PriorityFeeMarket()
        )
        # PRD US-011 line 840: optional bundle auction. When set, the bundle
        # pre-stage runs before the regular admit/scheduler path. Bundles are
        # submitted via :meth:`submit_bundle` and drained at the start of
        # each ``execute_slot`` call (slot-scoped, like the action queue).
        self._bundle_auction: BundleAuction | None = bundle_auction
        self._pending_bundles: list[Bundle] = []
        # PRD US-014 line 1108: rolling blockhash window. When set, ``admit()``
        # drops actions whose ``recent_blockhash`` falls outside the window
        # with reason ``BLOCKHASH_EXPIRED`` and ``execute_slot`` emits a
        # ``BlockhashExpiredEvent`` for each such drop. Stays ``None`` for
        # tests / chain-neutral callers that don't model blockhash expiry.
        self._blockhash_history: BlockhashHistory | None = blockhash_history
        # PRD US-014 line 1117: fork mechanism. When ``fork_spec`` is set,
        # ``execute_slot`` rolls a per-slot Bernoulli at probability
        # ``fork_probability_per_slot``; on a hit it picks a random
        # reorg depth in ``[1, max_reorg_depth_slots]`` and emits a
        # ``ForkReorgEvent``. Determinism: the dedicated ``_fork_rng`` is
        # seeded from ``ChainReorgForkSpec.seed`` so fork events are reproducible
        # independently of submission/landing RNG state.
        self._fork_spec: ChainReorgForkSpec | None = fork_spec
        # PRD US-014 line 1117: ``ChainReorgForkSpec.seed=None`` must NOT fall through
        # to ``random.Random(None)`` (system entropy). Treat None as a
        # deterministic default of 0 so any non-zero
        # ``fork_probability_per_slot`` is reproducible across runs.
        fork_seed: int = 0
        if fork_spec is not None and fork_spec.seed is not None:
            fork_seed = int(fork_spec.seed)
        self._fork_rng: random.Random = random.Random(fork_seed)
        # PRD US-014 line 1123: rolling per-slot history buffer feeding
        # the ``ForkReorgEvent`` payload (``abandoned_bundle_ids`` /
        # ``abandoned_actions_count``). Each entry is
        # ``(slot, admitted_actions_count, bundle_ids)``. Bound by
        # ``max_reorg_depth_slots + 1`` so a depth-d fork at slot N can
        # walk the inclusive range ``[N - d, N]`` (which is d + 1 slots).
        # Buffer is only populated when ``fork_spec`` is set; chain-neutral
        # callers pay zero overhead.
        history_capacity = (
            (fork_spec.max_reorg_depth_slots + 1) if fork_spec is not None else 0
        )
        self._slot_history: deque[tuple[int, int, tuple[str, ...]]] = deque(
            maxlen=max(1, history_capacity)
        )
        # Per-slot bundle telemetry, reset on each ``execute_slot``. The
        # snapshot wiring at PRD US-011 line 891 reads these.
        self._last_slot_selected_bundles: list[tuple[Bundle, BundleExecutionResult]] = []
        self._last_slot_dropped_bundles: list[tuple[Bundle, str]] = []

    @property
    def compute_budget(self) -> ComputeBudget:
        return self._compute_budget

    @property
    def priority_fee_market(self) -> PriorityFeeMarket:
        return self._priority_fee_market

    @property
    def bundle_auction(self) -> BundleAuction | None:
        return self._bundle_auction

    def submit_bundle(self, bundle: Bundle) -> None:
        """Queue a bundle for the next ``execute_slot`` invocation.

        Bundles are slot-scoped: ``execute_slot`` drains the queue and
        clears it whether the bundle was selected, dropped, or skipped for
        slot-CU reasons (PRD US-011 line 840). Callers re-submit each slot.
        """
        if self._bundle_auction is None:
            raise RuntimeError(
                "submit_bundle requires bundle_auction= to be set on SolanaLikeExecution"
            )
        self._pending_bundles.append(bundle)

    def _reserve_bundle_cu(self, cand: BundleCandidate) -> None:
        """Reserve slot and account CU for a selected bundle."""
        self._slot_cu_used += cand.total_cu
        for account in cand.write_locks:
            self._account_cu_tally[account] = (
                self._account_cu_tally.get(account, 0) + cand.total_cu
            )

    def _execute_selected_bundle(
        self,
        ctx: SlotContext,
        cand: BundleCandidate,
    ) -> list[ExecutedAction]:
        outcome = ctx.execute_bundle(cand.bundle, ctx.slot)
        # PRD US-011 line 867: tip-position semantics. Atomic rollback means
        # a revert at any position kills every tip regardless of placement.
        outcome.paid_tips = cand.bundle.paid_tip_payments(
            reverted=outcome.reverted,
            failed_at_index=outcome.failed_at_index,
        )
        self._last_slot_selected_bundles.append((cand.bundle, outcome))
        return list(outcome.executed)

    @staticmethod
    def _resolve_cu_limit(action: Action) -> int:
        cu = getattr(action, "compute_unit_limit", None)
        return int(cu) if cu is not None else DEFAULT_CU_LIMIT_FALLBACK

    @staticmethod
    def _propagate_wrapper_metadata(
        inner: Action, wrapper: VersionedTransaction
    ) -> None:
        """PRD US-009 line 657: copy wrapper-level metadata onto inner
        instructions that don't set their own.

        Solana's wire format puts ``recent_blockhash`` (and the implicit
        ~150-slot expiry derived from it) on the transaction envelope.
        Inner instructions therefore inherit the wrapper's blockhash for
        admit-time expiry purposes. Only fields the inner action leaves
        unset are filled in — explicit per-instruction values win.
        """
        if (
            getattr(inner, "recent_blockhash", None) is None
            and wrapper.recent_blockhash is not None
        ):
            try:
                inner.recent_blockhash = wrapper.recent_blockhash
            except (AttributeError, TypeError):
                pass
        if (
            getattr(inner, "expiry_slot", None) is None
            and wrapper.expiry_slot is not None
        ):
            try:
                inner.expiry_slot = wrapper.expiry_slot
            except (AttributeError, TypeError):
                pass

    def _is_blockhash_expired(self, action: Action, current_slot: int) -> bool:
        """PRD US-014 lines 1098-1108 admit-time expiry predicate.

        Resolves ``recent_blockhash=None`` to ``history.latest()`` and
        pins it back onto the action so a deferred re-admit re-uses the
        original blockhash rather than picking up an ever-fresher latest
        (which would make a None-blockhash action effectively immortal,
        violating PRD US-014 line 1098). Drops when:
          * the blockhash is unknown to the rolling history (evicted past
            the window or never recorded), OR
          * the explicit ``expiry_slot`` (when set) has been crossed, OR
          * the implicit ``blockhash_slot + validity_slots`` ceiling has
            been crossed.
        Returns False when the engine has no history yet (no slot has
        recorded a blockhash) — the action is admitted under the same
        "expiry not yet enforceable" rule the chain-neutral path uses.
        """
        history = self._blockhash_history
        if history is None:
            return False
        blockhash = getattr(action, "recent_blockhash", None)
        if blockhash is None:
            try:
                blockhash = history.latest()
            except LookupError:
                return False
            try:
                action.recent_blockhash = blockhash
            except (AttributeError, TypeError):
                pass
        blockhash_slot = history.slot_of(blockhash)
        if blockhash_slot is None:
            return True
        explicit_expiry = getattr(action, "expiry_slot", None)
        if explicit_expiry is not None:
            return current_slot > int(explicit_expiry)
        return current_slot - blockhash_slot > history.validity_slots

    def admit(
        self,
        actions: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> AdmissionResult:
        admitted: list[Action] = []
        dropped: list[tuple[Action, str]] = []
        per_tx_cap = self._compute_budget.per_tx
        priors = self._submission_priors
        history = self._blockhash_history

        # PRD US-009 line 657: the wrapper is what gets *submitted*; the
        # inner ``actions`` are what the engine *executes*. Run the
        # tx-size check on the wrapper, then unwrap into its inner
        # instructions so the rest of the admit pipeline (CU, blockhash,
        # submission-path) and downstream lock resolution / scheduling /
        # execution see the actions the way validators do — one inner
        # instruction at a time. Wrapper-level ``recent_blockhash`` and
        # ``expiry_slot`` propagate onto inner actions that don't set
        # their own.
        actions_to_admit: list[Action] = []
        for action in actions:
            if not isinstance(action, VersionedTransaction):
                actions_to_admit.append(action)
                continue
            if compute_tx_size(action, self._alts) > MAX_TX_SIZE_BYTES:
                dropped.append((action, DropReason.TX_SIZE_EXCEEDED))
                continue
            for inner in action.actions:
                self._propagate_wrapper_metadata(inner, action)
                actions_to_admit.append(inner)

        for action in actions_to_admit:
            # PRD US-014 lines 1098-1108: enforce both the rolling
            # blockhash-validity window AND any explicit ``expiry_slot``.
            # ``recent_blockhash=None`` means "use latest" — resolve to
            # ``history.latest()`` so the default path can't dodge expiry.
            # ``expiry_slot=None`` defaults to ``blockhash_slot + validity_slots``
            # (Solana's ~150-slot window); a non-None ``expiry_slot`` is
            # honored as a hard ceiling regardless of blockhash age.
            if history is not None:
                if self._is_blockhash_expired(action, round):
                    dropped.append((action, DropReason.BLOCKHASH_EXPIRED))
                    continue

            cu_limit = self._resolve_cu_limit(action)
            if cu_limit > per_tx_cap:
                dropped.append((action, DropReason.CU_PER_TX_EXCEEDED))
                continue

            # PRD US-004 line 379: jito_relayer path requires a Jito bundle,
            # which is submitted out-of-band via ``submit_bundle`` rather
            # than through ``admit``. Any individual ``Action`` arriving
            # here with ``submission_path == "jito_relayer"`` is therefore
            # structurally invalid (the per-action path was set without a
            # surrounding Bundle). The pre-existing ``BundleAction`` core
            # type is a multi-asset weighted basket trade — unrelated to
            # Jito bundles — so the test isn't ``isinstance(action,
            # BundleAction)``: it's "this action is not inside a bundle".
            path = getattr(action, "submission_path", "rpc")
            if path == "jito_relayer":
                dropped.append((action, DropReason.INVALID_SUBMISSION_PATH))
                continue

            # PRD US-004 line 364: sample Bernoulli(landing_prob) per action.
            # Skipped when no priors configured so the legacy (no-drop) path
            # is preserved for non-Solana-fidelity callers.
            if priors is not None:
                landing_prob = self._submission_landing_prob(path, priors)
                if landing_prob < 1.0 and self._submission_rng.random() >= landing_prob:
                    dropped.append((action, DropReason.SUBMISSION_PATH_DROP))
                    continue

            admitted.append(action)
        base_admitted, base_dropped = super().admit(admitted, round, context)
        return list(base_admitted), [*dropped, *base_dropped]

    @staticmethod
    def _submission_landing_prob(path: str, priors: SubmissionPathPriors) -> float:
        if path == "rpc":
            return priors.rpc_landing_prob_baseline
        if path == "tpu_quic":
            return priors.tpu_quic_landing_prob_baseline
        if path == "jito_relayer":
            return priors.jito_relayer_landing_prob_baseline
        return 1.0

    def supports_slot_execution(self) -> bool:
        return True

    def _run_bundle_pre_stage(
        self,
        ctx: SlotContext,
        ordered_locked: list[LockedAction],
    ) -> tuple[list[ExecutedAction], list[BundleCandidate]]:
        """Bundle auction admission, selection, and atomic execution.

        PRD US-011 line 840: runs before the regular scheduler phase.
        Drains ``self._pending_bundles`` (slot-scoped), folds auction drops
        into ``self._last_slot_dropped_bundles``, reserves CU for all selected
        bundles, executes pre-regular bundles via ``ctx.execute_bundle``, and
        returns post-regular candidates for the trading phase to execute after
        regular actions.
        """
        self._last_slot_selected_bundles = []
        self._last_slot_dropped_bundles = []
        if self._bundle_auction is None or not self._pending_bundles:
            self._pending_bundles.clear()
            return [], []
        if ctx.execute_bundle is None:
            # Engine has not wired the atomic-bundle executor yet (legacy
            # fixtures / non-Solana hosts). Drain bundles silently — the
            # auction is configured but the host can't run them safely.
            self._pending_bundles.clear()
            return [], []

        bundles = list(self._pending_bundles)
        self._pending_bundles.clear()
        admitted_bundles, admit_dropped = self._bundle_auction.admit(bundles)
        self._last_slot_dropped_bundles.extend(admit_dropped)

        # PRD US-013 line 1057: bundle landing rate aligns with the configured
        # submission-path priors (1.5). Apply Bernoulli sampling per bundle on
        # ``jito_relayer_landing_prob_baseline`` after auction admission, before
        # lock/CU selection, so every submitted bundle has the prior baked into
        # its observed landing rate. Drop reason mirrors the per-action sampler
        # so downstream telemetry sees one canonical reason string.
        if self._submission_priors is not None and admitted_bundles:
            landing_prob = self._submission_priors.jito_relayer_landing_prob_baseline
            if landing_prob < 1.0:
                survivors: list[Bundle] = []
                for bundle in admitted_bundles:
                    if self._submission_rng.random() >= landing_prob:
                        self._last_slot_dropped_bundles.append(
                            (bundle, DropReason.SUBMISSION_PATH_DROP)
                        )
                    else:
                        survivors.append(bundle)
                admitted_bundles = survivors

        candidates: list[BundleCandidate] = []
        for idx, bundle in enumerate(admitted_bundles):
            write_locks: set[AccountId] = set()
            read_locks: set[AccountId] = set()
            if ctx.resolve_locks is not None:
                for tx in bundle.txs:
                    for action in tx.actions:
                        resolved = ctx.resolve_locks(action)
                        if resolved is None:
                            continue
                        write_locks |= set(resolved.write_locks)
                        read_locks |= set(resolved.read_locks)
            # PRD US-013: resolve the locks held by any actions the bundle
            # declares it coexists with so the auction's non-bundle conflict
            # check exempts those locks for THIS candidate.
            coex_writes: set[AccountId] = set()
            coex_reads: set[AccountId] = set()
            if bundle.coexisting_actions and ctx.resolve_locks is not None:
                for coex_action in bundle.coexisting_actions:
                    resolved = ctx.resolve_locks(coex_action)
                    if resolved is None:
                        continue
                    coex_writes |= set(resolved.write_locks)
                    coex_reads |= set(resolved.read_locks)
            candidates.append(
                BundleCandidate(
                    bundle=bundle,
                    write_locks=frozenset(write_locks),
                    read_locks=frozenset(read_locks),
                    submitted_index=idx,
                    coexisting_write_locks=frozenset(coex_writes),
                    coexisting_read_locks=frozenset(coex_reads),
                )
            )

        # Only count the CU prefix of regular actions that will actually
        # fit per-slot CU as "non-bundle pending" locks. Actions past the
        # cap will be deferred by the trading-phase loop below and never
        # execute this slot, so feeding their write-locks to the bundle
        # auction would cause spurious ``bundle_lock_conflict`` drops
        # against actions that aren't really competing for the slot.
        per_slot_cap = self._compute_budget.per_slot
        nb_writes: set[AccountId] = set()
        nb_reads: set[AccountId] = set()
        nb_cu_used = self._slot_cu_used
        for locked in ordered_locked:
            cu_limit = self._resolve_cu_limit(locked.action)
            if nb_cu_used + cu_limit > per_slot_cap:
                break
            nb_cu_used += cu_limit
            nb_writes |= set(locked.write_locks)
            nb_reads |= set(locked.read_locks)

        result = self._bundle_auction.select_top_k(
            candidates,
            remaining_slot_cu=per_slot_cap - self._slot_cu_used,
            non_bundle_pending_writes=frozenset(nb_writes),
            non_bundle_pending_reads=frozenset(nb_reads),
        )
        self._last_slot_dropped_bundles.extend(result.dropped)

        executed: list[ExecutedAction] = []
        post_regular: list[BundleCandidate] = []
        for cand in result.selected:
            # Account for CU consumed by the bundle even on revert — the
            # validator still spends slot capacity replaying the bundle's
            # txs up to the failing position. The auction's own CU budget
            # check already used ``cand.total_cu`` so we mirror that here
            # for the regular-phase per-slot enforcement.
            self._reserve_bundle_cu(cand)
            if cand.bundle.execute_after_regular_actions:
                post_regular.append(cand)
                continue
            executed.extend(self._execute_selected_bundle(ctx, cand))

        return executed, post_regular

    def execute_slot(self, ctx: SlotContext) -> SlotOutcome:
        # PRD 1.0 phase-bucket preservation: admit and order run ONCE on the
        # full slot union (matches legacy ``_execute_round`` admit/order on the
        # mixed trading+LP set). Splitting trading vs LP happens AFTER ordering
        # so a slot-wide admission policy or global ordering pass sees every
        # action. The engine-supplied ``run_liquidations`` callback runs the
        # LIQUIDATION phase between trading and LP execution.
        self._current_slot = ctx.slot
        self._slot_cu_used = 0
        self._account_cu_tally = {}
        if ctx.ordering_context is not None:
            ctx.ordering_context.current_slot = ctx.slot
            if self._leader_schedule is not None:
                ctx.ordering_context.current_leader = self._leader_schedule.leader_for_slot(ctx.slot)
        if isinstance(ctx.slot_event, SlotSkippedEvent):
            scheduled_leader = ctx.slot_event.scheduled_leader
            if scheduled_leader is None and self._leader_schedule is not None:
                scheduled_leader = self._leader_schedule.leader_for_slot(ctx.slot)
            ctx.emit(Event(
                type=EventType.SLOT_SKIPPED,
                round=ctx.slot,
                timestamp=0,
                data={"slot": ctx.slot, "scheduled_leader": scheduled_leader},
            ))
            outcome = SlotOutcome(
                slot=ctx.slot,
                admitted=[],
                dropped=[],
                deferred=list(ctx.pending_actions),
                executed=[],
                events=[],
            )
            self.on_slot_end(outcome)
            return outcome
        admitted, dropped = self.admit(ctx.pending_actions, ctx.slot, ctx.ordering_context)
        # PRD US-002 line 154/165: emit a ComputeBudgetExhaustedEvent for each
        # admit-time per-tx cap drop so downstream telemetry can correlate the
        # canonical drop reason with the offending action and remaining budget.
        per_tx_cap = self._compute_budget.per_tx
        for dropped_action, reason in dropped:
            if reason == DropReason.CU_PER_TX_EXCEEDED:
                payload = ComputeBudgetExhaustedEvent(
                    slot=ctx.slot,
                    offender=getattr(dropped_action, "agent_id", ""),
                    action=dropped_action,
                    budget_kind="per_tx",
                    remaining=per_tx_cap,
                    attempted=self._resolve_cu_limit(dropped_action),
                )
                ctx.emit(Event(
                    type=EventType.COMPUTE_BUDGET_EXHAUSTED,
                    round=ctx.slot,
                    timestamp=0,
                    data={"compute_budget_exhausted": payload, "budget_kind": "per_tx"},
                ))
            elif reason == DropReason.BLOCKHASH_EXPIRED:
                # PRD US-014 line 1108: surface the admit-time expiry drop
                # via a typed event so telemetry / inspector can correlate
                # the offending action with the stale blockhash.
                blockhash = getattr(dropped_action, "recent_blockhash", "")
                bh_payload = BlockhashExpiredEvent(
                    slot=ctx.slot,
                    action=dropped_action,
                    blockhash=blockhash if blockhash is not None else "",
                )
                ctx.emit(Event(
                    type=EventType.BLOCKHASH_EXPIRED,
                    round=ctx.slot,
                    timestamp=0,
                    data={"blockhash_expired": bh_payload, "blockhash": bh_payload.blockhash},
                ))
        ordered = self.order(admitted, ctx.slot, ctx.ordering_context)

        # PRD US-003 step 4: lock resolution runs before the scheduler. The
        # engine supplies a per-action resolver via ``ctx.resolve_locks``
        # (``SimulationEngine._resolve_action_locks``); test fixtures that
        # bypass the engine fall back to empty-lock wrapping here.
        resolver = ctx.resolve_locks
        ordered_locked: list[LockedAction] = []
        for a in ordered:
            if resolver is not None:
                resolved = resolver(a)
                if resolved is None:
                    # Strict admission rejection lands with the sandwich
                    # tests (PRD test list line 305); preserved as a code
                    # path here so the wiring is testable when callers opt
                    # in by returning None for unresolvable actions.
                    dropped.append((a, DropReason.MISSING_LOCK_RESOLVER))
                    continue
                ordered_locked.append(resolved)
            else:
                ordered_locked.append(LockedAction(action=a))

        # PRD US-011 line 840: bundle pre-stage. The auction selects bundles
        # and reserves their CU before the regular scheduler. Most bundles
        # execute immediately; back-run bundles can opt into post-regular
        # execution so they run after the victim while still participating in
        # the same slot auction.
        bundle_executed, post_regular_bundles = self._run_bundle_pre_stage(
            ctx, ordered_locked
        )

        # Per-slot CU enforcement: walk in execution order, tally CU, and
        # defer actions that would push the slot over ``budget.per_slot``.
        # PRD US-008 line 614: also enforce ``budget.per_writable_account``
        # for each account in ``locked.write_locks`` — a hot account whose
        # running tally would overflow defers the action with
        # ``cu_per_account_exceeded`` and emits a ComputeBudgetExhaustedEvent
        # with ``budget_kind="per_writable_account"``.
        per_slot_cap = self._compute_budget.per_slot
        per_account_cap = self._compute_budget.per_writable_account
        kept_locked: list[LockedAction] = []
        deferred: list[Action] = []
        for locked in ordered_locked:
            action = locked.action
            cu_limit = self._resolve_cu_limit(action)
            if self._slot_cu_used + cu_limit > per_slot_cap:
                deferred.append(action)
                payload = ComputeBudgetExhaustedEvent(
                    slot=ctx.slot,
                    offender=getattr(action, "agent_id", ""),
                    action=action,
                    budget_kind="per_slot",
                    remaining=per_slot_cap - self._slot_cu_used,
                    attempted=cu_limit,
                )
                ctx.emit(Event(
                    type=EventType.COMPUTE_BUDGET_EXHAUSTED,
                    round=ctx.slot,
                    timestamp=0,
                    data={"compute_budget_exhausted": payload, "budget_kind": "per_slot"},
                ))
                continue
            overflow_account: AccountId | None = None
            # Sort write_locks so the picked overflow account is stable across
            # Python processes — frozenset iteration follows hash() order which
            # is PYTHONHASHSEED-randomized for strings.
            for account in sorted(locked.write_locks):
                if self._account_cu_tally.get(account, 0) + cu_limit > per_account_cap:
                    overflow_account = account
                    break
            if overflow_account is not None:
                deferred.append(action)
                payload = ComputeBudgetExhaustedEvent(
                    slot=ctx.slot,
                    offender=getattr(action, "agent_id", ""),
                    action=action,
                    budget_kind="per_writable_account",
                    remaining=per_account_cap - self._account_cu_tally.get(overflow_account, 0),
                    attempted=cu_limit,
                )
                ctx.emit(Event(
                    type=EventType.COMPUTE_BUDGET_EXHAUSTED,
                    round=ctx.slot,
                    timestamp=0,
                    data={
                        "compute_budget_exhausted": payload,
                        "budget_kind": "per_writable_account",
                        "account": overflow_account,
                    },
                ))
                continue
            self._slot_cu_used += cu_limit
            for account in locked.write_locks:
                self._account_cu_tally[account] = (
                    self._account_cu_tally.get(account, 0) + cu_limit
                )
            kept_locked.append(locked)
        kept: list[Action] = [locked.action for locked in kept_locked]

        # PRD US-010 line 738: update the priority fee market on every admitted
        # locked action's write-lock set. Read-locks are observational only and
        # never move the market (PRD line 743).
        # PRD US-010 line 745: snapshot the prior percentiles per touched
        # account, observe, then emit ``PriorityFeeMarketUpdatedEvent`` for any
        # account whose distribution shifted by more than the configured
        # relative threshold (default 5%) — keeps event volume manageable.
        touched_accounts: set[AccountId] = set()
        for locked in kept_locked:
            touched_accounts.update(locked.write_locks)
        prior_percentiles: dict[AccountId, dict[int, int] | None] = {}
        for account in touched_accounts:
            prior_percentiles[account] = (
                self._priority_fee_market.previous_percentiles(account)
            )
        for locked in kept_locked:
            cu_price = locked.action.compute_unit_price_micro_lamports or 0
            for account in locked.write_locks:
                self._priority_fee_market.observe(account, ctx.slot, cu_price)
        threshold = self._priority_fee_market.update_event_threshold
        # Sort so event emission order is stable across Python processes.
        for account in sorted(touched_accounts):
            new_percentiles = self._priority_fee_market.percentiles(account)
            prior = prior_percentiles[account]
            if not _percentiles_shifted_more_than(prior, new_percentiles, threshold):
                continue
            payload = PriorityFeeMarketUpdatedEvent(
                slot=ctx.slot,
                account_id=account,
                percentiles=new_percentiles,
                previous_percentiles=prior,
                threshold=threshold,
            )
            ctx.emit(Event(
                type=EventType.PRIORITY_FEE_MARKET_UPDATED,
                round=ctx.slot,
                timestamp=0,
                data={
                    "priority_fee_market_updated": payload,
                    "account_id": account,
                },
            ))

        # PRD 1.2a (line 167) defer semantics: until 1.12 wires
        # recent_blockhash expiry an action whose CU footprint never fits
        # will be re-queued indefinitely. Track per-action consecutive defer
        # counts and log a warning the first time the threshold is crossed
        # so the situation is observable. Bookkeeping is keyed on id(action)
        # because Action is non-frozen (so non-hashable) and the engine
        # re-queues the same instance into next slot's pending. Stale ids
        # for actions seen this slot but not deferred (executed or admit-
        # dropped) are evicted to prevent unbounded growth.
        deferred_ids = {id(a) for a in deferred}
        seen_ids = {id(a) for a in ctx.pending_actions}
        for action in deferred:
            count = self._defer_counts.get(id(action), 0) + 1
            self._defer_counts[id(action)] = count
            if count == _DEFER_WARNING_THRESHOLD:
                _LOGGER.warning(
                    "action %s from agent %r deferred %d slots in a row "
                    "(cu_limit=%d, per_slot_cap=%d); blockhash expiry not "
                    "yet wired (PRD 1.12) so this action may stay in queue "
                    "indefinitely",
                    type(action).__name__,
                    getattr(action, "agent_id", None),
                    count,
                    self._resolve_cu_limit(action),
                    per_slot_cap,
                )
        for stale in seen_ids - deferred_ids:
            self._defer_counts.pop(stale, None)

        # Split kept LockedActions into trading vs LP phases by inspecting
        # the underlying action type. Lock metadata follows the action.
        trading_locked = [locked for locked in kept_locked if not is_lp_action(locked.action)]
        lp_locked = [locked for locked in kept_locked if is_lp_action(locked.action)]

        # PRD US-011 line 840: selected pre-regular bundles execute first;
        # post-regular bundles are appended after normal trading below.
        executed: list[ExecutedAction] = list(bundle_executed)
        for lane in self._scheduler.schedule(trading_locked, ctx.slot, state=None):
            ordered_lane = self._ordering.order_locked(
                lane.actions, ctx.slot, ctx.ordering_context
            )
            for locked in ordered_lane:
                executed.append(ctx.executor(locked.action, ctx.slot))

        for cand in post_regular_bundles:
            executed.extend(self._execute_selected_bundle(ctx, cand))

        ctx.run_liquidations()

        for lane in self._scheduler.schedule(lp_locked, ctx.slot, state=None):
            ordered_lane = self._ordering.order_locked(
                lane.actions, ctx.slot, ctx.ordering_context
            )
            for locked in ordered_lane:
                executed.append(ctx.executor(locked.action, ctx.slot))

        # PRD US-014 line 1117: per-slot fork roll. Runs at end-of-slot so
        # ``[fork_point_slot - depth, fork_point_slot]`` (PRD line 1119) can
        # be walked over the rolling history buffer that includes the
        # current slot's just-completed admit + bundle execution. Sample
        # Bernoulli at ``fork_probability_per_slot``; on a hit, pick depth
        # in ``[1, max_reorg_depth_slots]`` and emit ``ForkReorgEvent``
        # populated from ``_slot_history``. State-revert / tip-revert
        # (PRD line 1124) land in a follow-up iteration.
        if self._fork_spec is not None:
            bundle_ids_this_slot: tuple[str, ...] = tuple(
                f"{ctx.slot}:{i}" for i in range(len(self._last_slot_selected_bundles))
            )
            self._slot_history.append(
                (ctx.slot, len(admitted), bundle_ids_this_slot)
            )
            if self._fork_spec.fork_probability_per_slot > 0.0 and (
                self._fork_rng.random() < self._fork_spec.fork_probability_per_slot
            ):
                max_depth = max(1, int(self._fork_spec.max_reorg_depth_slots))
                depth = self._fork_rng.randint(1, max_depth)
                min_slot = ctx.slot - depth
                abandoned_actions_count = 0
                abandoned_bundle_ids: list[str] = []
                for slot, actions_count, bundle_ids in self._slot_history:
                    if min_slot <= slot <= ctx.slot:
                        abandoned_actions_count += actions_count
                        abandoned_bundle_ids.extend(bundle_ids)
                fork_payload = ForkReorgEvent(
                    fork_point_slot=ctx.slot,
                    depth=depth,
                    abandoned_bundle_ids=tuple(abandoned_bundle_ids),
                    abandoned_actions_count=abandoned_actions_count,
                )
                ctx.emit(Event(
                    type=EventType.FORK_REORG,
                    round=ctx.slot,
                    timestamp=0,
                    data={
                        "fork_reorg": fork_payload,
                        "fork_point_slot": ctx.slot,
                        "depth": depth,
                    },
                ))
                # PRD US-014 line 1124: tip-revert under fork. Bundles that
                # landed in the current slot fall in the abandoned range
                # ``[ctx.slot - depth, ctx.slot]`` (depth >= 1, current slot
                # always inclusive). Mark them reverted and clear paid_tips
                # so ``SimulationEngine._credit_validator_revenue`` skips
                # them and ``_collect_bundle_outcomes`` reports zero revenue.
                # Past-slot tip-revert (debiting already-credited revenue
                # from validator agents) is engine-level and lands later.
                for _bundle, result in self._last_slot_selected_bundles:
                    result.reverted = True
                    result.paid_tips = []

        outcome = SlotOutcome(
            slot=ctx.slot,
            admitted=list(admitted),
            dropped=list(dropped),
            deferred=deferred,
            executed=executed,
            events=[],
        )
        self.on_slot_end(outcome)
        return outcome

    def on_slot_end(self, outcome: SlotOutcome) -> None:
        """Forward slot-end to the legacy cost-model evolution hook.

        Slot-aware models receive ``on_slot_end`` instead of ``on_round_end``
        (PRD 1.0). Fires exactly ONCE per slot (after both trading and LP
        phases) so cost models like EIP1559 see the same total action count
        the legacy ``on_round_end(num_actions)`` saw — preserving bit-identical
        base-fee evolution.
        """
        updater = getattr(self._cost_model, "update_base_fee", None)
        if callable(updater):
            updater(len(outcome.executed))


def _serialize_cost_model(model: TransactionCostModel) -> dict[str, Any]:
    if isinstance(model, ZeroCost):
        return {"type": "zero"}
    if isinstance(model, FixedCost):
        return {"type": "fixed", "cost": model._cost}
    if isinstance(model, TypedCost):
        return {
            "type": "typed",
            "default_cost": model._default,
            "costs": [
                {
                    "action_type": serialize_callable_ref(action_type),
                    "cost": cost,
                }
                for action_type, cost in model._costs.items()
            ],
        }
    if isinstance(model, EIP1559Cost):
        return {
            "type": "eip1559",
            "base_fee": model._base_fee,
            "target_actions_per_round": model._target,
            "adjustment_factor": model._factor,
        }
    if isinstance(model, ComputeUnitCost):
        return {
            "type": "compute_unit",
            "default_units": model._default_units,
            "base_cost": model._base_cost,
            "unit_costs": [
                {
                    "action_type": serialize_callable_ref(action_type),
                    "cost": cost,
                }
                for action_type, cost in model._unit_costs.items()
            ],
        }
    raise TypeError(f"execution cost model {type(model).__name__} is not snapshot-serializable")


def _deserialize_cost_model(data: dict[str, Any]) -> TransactionCostModel:
    cost_type = data["type"]
    if cost_type == "zero":
        return ZeroCost()
    if cost_type == "fixed":
        return FixedCost(data["cost"])
    if cost_type == "typed":
        costs = {
            deserialize_callable_ref(entry["action_type"]): entry["cost"]
            for entry in data.get("costs", [])
        }
        return TypedCost(costs=costs, default_cost=data.get("default_cost", 0))
    if cost_type == "eip1559":
        return EIP1559Cost(
            base_fee=data["base_fee"],
            target_actions_per_round=data.get("target_actions_per_round", 50),
            adjustment_factor=data.get("adjustment_factor", 8),
        )
    if cost_type == "compute_unit":
        unit_costs = {
            deserialize_callable_ref(entry["action_type"]): entry["cost"]
            for entry in data.get("unit_costs", [])
        }
        return ComputeUnitCost(
            unit_costs=unit_costs,
            default_units=data.get("default_units", 1),
            base_cost=data.get("base_cost", 0),
        )
    raise ValueError(f"unknown execution cost model type: {cost_type}")


def _serialize_ordering(ordering: OrderingStrategy) -> dict[str, Any]:
    if isinstance(ordering, FIFOOrdering):
        return {"type": "fifo"}
    if isinstance(ordering, RandomOrdering):
        return {"type": "random"}
    if isinstance(ordering, PriorityOrdering):
        return {"type": "priority"}
    if isinstance(ordering, SandwichOrdering):
        return {
            "type": "sandwich",
            "adversarial_agent_ids": list(ordering._adversarial),
            "target_agent_ids": list(ordering._targets),
        }
    if isinstance(ordering, BlockBuilder):
        strategy_ref = serialize_callable_ref(ordering._strategy)
        if strategy_ref is None:
            raise TypeError("BlockBuilder strategy must be a top-level importable callable for snapshots")
        return {
            "type": "block_builder",
            "builder_agent_id": ordering._builder_id,
            "strategy_ref": strategy_ref,
        }
    raise TypeError(f"ordering strategy {type(ordering).__name__} is not snapshot-serializable")


def _deserialize_ordering(data: dict[str, Any]) -> OrderingStrategy:
    ordering_type = data["type"]
    if ordering_type == "fifo":
        return FIFOOrdering()
    if ordering_type == "random":
        return RandomOrdering()
    if ordering_type == "priority":
        return PriorityOrdering()
    if ordering_type == "sandwich":
        return SandwichOrdering(
            adversarial_agent_ids=set(data.get("adversarial_agent_ids", [])),
            target_agent_ids=set(data.get("target_agent_ids", [])),
        )
    if ordering_type == "block_builder":
        return BlockBuilder(
            builder_agent_id=data["builder_agent_id"],
            strategy=deserialize_callable_ref(data["strategy_ref"]),
        )
    raise ValueError(f"unknown ordering strategy type: {ordering_type}")


def attach_ordering_rng(execution_model: ExecutionModel, rng: Any) -> None:
    ordering = getattr(execution_model, "_ordering", None)
    if isinstance(ordering, RandomOrdering):
        ordering._rng = rng


def attach_submission_rng(execution_model: ExecutionModel, rng: np.random.Generator) -> None:
    """Thread a seeded RNG into the submission-path Bernoulli sampler.

    PRD US-004 line 366: the engine's top-level seed is spawned into a child
    seed for ``SolanaLikeExecution._submission_rng`` so per-slot drop counts
    are reproducible across runs.
    """
    if isinstance(execution_model, SolanaLikeExecution):
        execution_model._submission_rng = rng


def attach_alts(
    execution_model: ExecutionModel,
    alts: Mapping[AltId, AddressLookupTable],
) -> None:
    """Wire the engine's ALT registry into the admit-time size check.

    PRD US-009 line 684: the validation that a 30-account VersionedTransaction
    is admitted when covered by a 30-entry ALT requires ``compute_tx_size`` to
    see the same ALTs the engine seeded from the spec.
    """
    if isinstance(execution_model, SolanaLikeExecution):
        execution_model._alts = dict(alts)


def serialize_execution_model(model: ExecutionModel) -> dict[str, Any]:
    common = {
        "ordering": _serialize_ordering(getattr(model, "_ordering")),
        "cost_model": _serialize_cost_model(getattr(model, "_cost_model")),
        "cost_token": getattr(model, "_cost_token"),
        "refund_failed_costs": getattr(model, "_refund_failed_costs", False),
    }

    if isinstance(model, SolanaLikeExecution):
        return {
            "type": "solana_like",
            **common,
            "visible_roles": list(getattr(model, "_visible_roles", set())),
            "scheduler": serialize_scheduler(getattr(model, "_scheduler", SerialScheduler())),
        }
    if isinstance(model, BatchExecution):
        queue_visibility_ref = serialize_callable_ref(getattr(model, "_queue_visibility", None))
        admission_policy_ref = serialize_callable_ref(getattr(model, "_admission_policy", None))
        if getattr(model, "_queue_visibility", None) is not None and queue_visibility_ref is None:
            raise TypeError("BatchExecution queue_visibility must be importable for snapshots")
        if getattr(model, "_admission_policy", None) is not None and admission_policy_ref is None:
            raise TypeError("BatchExecution admission_policy must be importable for snapshots")
        return {
            "type": "batch",
            **common,
            "queue_visibility_ref": queue_visibility_ref,
            "admission_policy_ref": admission_policy_ref,
        }
    if isinstance(model, DirectExecution):
        return {
            "type": "direct",
            **common,
            "expose_pending_actions": getattr(model, "_expose_pending", False),
        }
    raise TypeError(f"execution model {type(model).__name__} is not snapshot-serializable")


def deserialize_execution_model(data: dict[str, Any]) -> ExecutionModel:
    ordering = _deserialize_ordering(data["ordering"])
    cost_model = _deserialize_cost_model(data["cost_model"])
    cost_token = data.get("cost_token", "COLLATERAL")
    refund_failed_costs = data.get("refund_failed_costs", False)
    model_type = data["type"]

    if model_type == "solana_like":
        # Legacy snapshots without the ``scheduler`` field default to
        # ``SerialScheduler`` for backwards compatibility (PRD US-002).
        sched_data = data.get("scheduler")
        scheduler = deserialize_scheduler(sched_data) if sched_data is not None else SerialScheduler()
        return SolanaLikeExecution(
            cost_model=cost_model,
            cost_token=cost_token,
            ordering=ordering,
            visible_roles=set(data.get("visible_roles", [])),
            scheduler=scheduler,
        )
    if model_type == "batch":
        return BatchExecution(
            ordering=ordering,
            cost_model=cost_model,
            cost_token=cost_token,
            queue_visibility=deserialize_callable_ref(data.get("queue_visibility_ref")),
            admission_policy=deserialize_callable_ref(data.get("admission_policy_ref")),
            refund_failed_costs=refund_failed_costs,
        )
    if model_type == "direct":
        return DirectExecution(
            ordering=ordering,
            cost_model=cost_model,
            cost_token=cost_token,
            expose_pending_actions=data.get("expose_pending_actions", False),
            refund_failed_costs=refund_failed_costs,
        )
    raise ValueError(f"unknown execution model type: {model_type}")
