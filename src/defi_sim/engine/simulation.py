"""SimulationEngine — protocol-agnostic simulation runner.

Operates in two modes:
1. Single-market mode: Market + plain Actions
2. World mode: World + MultiMarketActions
"""

from __future__ import annotations

import copy
import hashlib
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import numpy as np

from defi_sim.core.agent import (
    Agent,
    DecisionContext,
    DelayedInformation,
    FullTransparency,
)
from defi_sim.core.clock import BlockClock, Clock, SolanaSlotClock
from defi_sim.core.market import (
    ConcentratedLPPosition,
    DerivativesMarket,
    LendingMarket,
    Liquidatable,
    LiquidityPool,
    Market,
    PricedMarket,
)
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentState,
    AtomicAction,
    BundleOutcome,
    BundleTipPaidEvent,
    BundleTipRevertedEvent,
    ConditionalAction,
    ExecutionContext,
    ExecutionResult,
    FlashLoanAction,
    LPAction,
    LiquidateAction,
    MarketSnapshot,
    MultiMarketAction,
    Numeric,
    RoundSnapshot,
    SimulationResult,
    TokenId,
    ValidatorEpochRevenue,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.execution import (
    BatchExecution,
    ExecutionModel,
    SolanaLikeExecution,
    attach_alts,
    attach_ordering_rng,
    attach_submission_rng,
)
from defi_sim.engine.events import Event, EventBus, EventType
from defi_sim.engine.lst import advance_lst_rate
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.parameters import ParameterStore
from defi_sim.engine.scheduler import LockedAction, LockResolver
from defi_sim.engine.bundle import Bundle
from defi_sim.engine.slot import BundleExecutionResult, ExecutedAction, SlotContext
from defi_sim.engine.world import World, WorldContext


def _stable_entropy(agent_id: AgentId) -> tuple[int, int]:
    raw = hashlib.blake2b(repr(agent_id).encode("utf-8"), digest_size=8).digest()
    entropy = int.from_bytes(raw, "little", signed=False)
    return entropy & 0xFFFFFFFF, (entropy >> 32) & 0xFFFFFFFF


@dataclass(frozen=True)
class _PlannedAction:
    action: Action
    execution_cost: Numeric
    cost_token: TokenId | None


@dataclass
class AtomicBoundary:
    """Control handle yielded by ``SimulationEngine.atomic_state_boundary``.

    Call ``rollback()`` to request that the boundary restore the pre-entry
    snapshot on exit. Exceptions propagating out of the ``with`` block always
    trigger a restore regardless.
    """

    should_rollback: bool = False

    def rollback(self) -> None:
        self.should_rollback = True


class SimulationEngine:
    """Protocol-agnostic simulation runner."""

    def __init__(
        self,
        market: Market | World,
        agents: list[Agent],
        config: SimulationConfig,
        event_bus: EventBus | None = None,
    ):
        self._is_world = isinstance(market, World)
        self._market: Market | World = market
        self._agents = agents
        self._config = config
        self._bus = event_bus or EventBus()
        self._register_agent_event_handlers()
        if self._is_world:
            self._market.attach_event_bus(
                self._bus,
                round_provider=lambda: self._current_round,
                timestamp_provider=lambda: self._clock.timestamp(self._current_round),
            )

        # RNG tree. Spawn count grew from 4 to 5 in US-004 to add the
        # submission-path Bernoulli sampler. SeedSequence.spawn is
        # deterministic in its prefix so existing seeds [0..3] are
        # bit-identical pre/post the addition.
        seq = np.random.SeedSequence(config.seed)
        child_seeds = seq.spawn(5)
        self._agent_rng = np.random.default_rng(child_seeds[0])
        self._ordering_rng = np.random.default_rng(child_seeds[1])
        self._feed_rng = np.random.default_rng(child_seeds[2])
        self._engine_rng = np.random.default_rng(child_seeds[3])
        self._submission_rng = np.random.default_rng(child_seeds[4])

        # Clock
        self._clock: Clock = config.clock or BlockClock()

        # Execution layer
        self._execution_model: ExecutionModel = config.execution_model
        attach_ordering_rng(self._execution_model, self._ordering_rng)
        attach_submission_rng(self._execution_model, self._submission_rng)

        # Parameters
        self._parameters: ParameterStore = config.parameters or ParameterStore()

        # Information filter
        self._info_filter = config.information_filter or FullTransparency()

        # State
        self._current_round = 0
        self._stopped_early = False
        self._cancelled = False
        self._stop_reason: str | None = None
        self._started = False
        # Per-action capture used by the slot executor closure to harvest
        # success/failure from the void _execute_action chain. Non-None only
        # while a single action is in flight inside execute_slot().
        self._slot_action_capture: dict[str, Any] | None = None
        self._fee_destination_balances: dict[str, dict[TokenId, Numeric]] = {}
        self._pending_lp_fees: dict[int, dict[TokenId, Numeric]] = {}
        self._last_feed_prices: dict[TokenId, Numeric] | None = None
        self._round_feed_prices: dict[TokenId, Numeric] | None = None
        self._price_history: list[dict[TokenId, Numeric]] = []
        # `fee_history[round][destination][token_id]` — fees are kept
        # keyed by token so mixed-market / mixed-collateral runs don't
        # collapse different tokens into a single scalar (5 USDC + 2 ETH
        # must not read as 7). Downstream charts decide how to render.
        self._fee_history: list[dict[str, dict[TokenId, Numeric]]] = []
        self._round_fee_splits: dict[str, dict[TokenId, Numeric]] = {}
        self._snapshots: list[RoundSnapshot] = []
        # PRD US-012 line 969: per-(epoch, validator-pubkey) MEV revenue.
        # Populated by ``_credit_validator_revenue`` on each leader slot.
        self._validator_revenue_by_epoch: dict[int, dict[str, ValidatorEpochRevenue]] = {}
        # PRD US-014 line 1119: rolling pre-slot snapshots used to revert
        # state when a fork roll abandons slots ``[N-d, N]``. Each entry is
        # ``(slot, snapshot_dict)`` produced by
        # ``_snapshot_bundle_mutable_state`` immediately before the slot's
        # ``execute_slot`` runs. The buffer is bounded by
        # ``max_reorg_depth_slots + 1`` so a depth-d fork at slot N can
        # locate the snapshot for slot ``N - d`` (inclusive abandonment
        # range). Lazy-populated only when the execution model carries a
        # ``ChainReorgForkSpec``; chain-neutral runs pay zero overhead.
        self._fork_state_snapshots: deque[tuple[int, dict[str, Any]]] = deque()
        # PRD US-014 line 1120: per-slot admitted non-bundle actions kept
        # alongside the snapshot ring. On a reorg with depth ``d`` we
        # replay actions admitted in slots ``[N-d, N]`` (in original
        # order) by prepending them to ``_deferred_carryover``. Bundles
        # from the abandoned slots are NOT replayed (they revert).
        self._fork_admitted_actions: deque[tuple[int, list[Action]]] = deque()
        # PRD US-002 line 128 + line 167: actions deferred by the per-slot
        # CU enforcement carry over into the next slot's ``pending_actions``
        # so the engine — not the agent — is responsible for retrying them.
        # Their ``recent_blockhash`` is re-checked at the next slot's
        # ``admit()`` (PRD US-014 line 1108) so stale deferrals expire
        # naturally instead of accumulating indefinitely.
        self._deferred_carryover: list[Action] = []
        # PRD US-011 line 839 / US-005: durable per-slot bundle tip ledger.
        # Each landed (non-reverted) bundle appends one entry; the ledger
        # is included in ``_snapshot_bundle_mutable_state`` so atomic
        # rollbacks (PRD US-005 line 410) and fork reverts (PRD US-014 line
        # 1124) drop entries from abandoned slots alongside the rest of
        # the engine's mutable state.
        self._tip_outcomes: list[BundleTipPaidEvent] = []

        # PRD US-004 line 368: per-agent drop reasons from the previous slot.
        # Surfaced on ``DecisionContext.last_drop_reasons`` so agents can
        # observe their own dropped actions; consumed by the agent loop to
        # call ``Agent.should_resubmit_on_drop`` for opt-in re-submission.
        self._last_drop_reasons_by_agent: dict[AgentId, dict[str, list[Action]]] = {}
        # Buffer for the *current* slot's drops; rotated into
        # ``_last_drop_reasons_by_agent`` at the next slot boundary so
        # agents see them via ``DecisionContext.last_drop_reasons`` exactly
        # one slot after the drop, matching PRD line 368's "next-slot"
        # observation.
        self._next_drop_reasons_by_agent: dict[AgentId, dict[str, list[Action]]] = {}

        # PRD US-006 line 494-496: oracle telemetry. ``_pull_oracles`` and
        # ``_push_oracles`` are populated by callers via
        # :meth:`register_oracle`; ``_oracle_pull_slots`` records the slots
        # at which each pull oracle was actually refreshed (drives
        # ``oracle_costs_per_slot`` aggregation in ``_build_result``).
        # ``_oracle_stale_emitted`` deduplicates stale-event emissions so
        # consumers see one event per (oracle, contiguous-stale-window).
        self._pull_oracles: dict[str, Any] = {}
        self._push_oracles: dict[str, Any] = {}
        self._oracle_pull_slots: dict[str, list[int]] = {}
        self._oracle_stale_emitted: dict[str, int] = {}

        # Initialize agent states with collateral if not set
        self._agent_rngs: dict[AgentId, np.random.Generator] = {}
        for agent in agents:
            if not hasattr(agent, 'state') or agent.state is None:
                agent.state = AgentState(agent_id=agent.agent_id)
            entropy_lo, entropy_hi = _stable_entropy(agent.agent_id)
            agent_rng = np.random.default_rng(np.random.SeedSequence([config.seed, entropy_lo, entropy_hi]))
            self._agent_rngs[agent.agent_id] = agent_rng
            if hasattr(agent, "_rng"):
                agent._rng = agent_rng

        self._configure_numeric_mode()
        self._attach_feed_rngs()

        # AddressLookupTable registry (US-009, PRD line 676). Seeded from
        # ``config.alts`` so VersionedTransactions can resolve their account
        # references against these tables when computing wire-format size.
        from defi_sim.engine.transactions import AddressLookupTable, AltId
        self.alts: dict[AltId, AddressLookupTable] = {
            alt.id: alt for alt in (config.alts or [])
        }
        attach_alts(self._execution_model, self.alts)

        # PRD US-012 line 963: when Validator agents are present in the spec,
        # prefer LeaderSchedule.from_validator_agents over a manually-supplied
        # schedule so the leader rotation tracks the validator-set stake
        # weights authoritatively.
        from defi_sim.agents.validator import Validator
        from defi_sim.engine.leader_schedule import LeaderSchedule
        validator_agents = [a for a in agents if isinstance(a, Validator)]
        if validator_agents and isinstance(self._execution_model, BatchExecution):
            existing = getattr(self._execution_model, "_leader_schedule", None)
            seed = existing._seed if existing is not None else 0
            # PRD US-012 line 963: epoch_length_slots must track whatever the
            # caller configured. Prefer (in order): the existing schedule the
            # execution model already carries, the active SolanaSlotClock's
            # ``epoch_length_slots``, then the mainnet default. Without this
            # the rebuild silently overwrites a user-supplied epoch length
            # with 432_000 whenever no preexisting schedule was attached.
            from defi_sim.core.clock import SolanaSlotClock
            if existing is not None:
                epoch_length_slots = existing._epoch_length_slots
            elif isinstance(self._clock, SolanaSlotClock):
                epoch_length_slots = self._clock.epoch_length_slots
            else:
                epoch_length_slots = 432_000
            self._execution_model._leader_schedule = (
                LeaderSchedule.from_validator_agents(
                    validator_agents,
                    seed=seed,
                    epoch_length_slots=epoch_length_slots,
                )
            )

        # Per-LST RNGs for ExchangeRateDriftSpec.volatility_per_epoch noise.
        # If the spec carries an explicit `seed`, use it directly; otherwise
        # derive a deterministic per-token seed from the global config seed
        # so reproducibility is preserved without requiring per-token seeds.
        self._lst_rngs: dict[str, np.random.Generator] = {}
        for token in (config.lst_tokens or []):
            drift = getattr(token, "exchange_rate_drift", None)
            if drift is None:
                continue
            seed = drift.seed if drift.seed is not None else config.seed
            self._lst_rngs[token.id] = np.random.default_rng(
                np.random.SeedSequence([seed, hash(token.id) & 0xFFFFFFFF])
            )

    @property
    def current_round(self) -> int:
        return self._current_round

    @property
    def validator_revenue_by_epoch(self) -> dict[int, dict[str, ValidatorEpochRevenue]]:
        """PRD US-012 line 969: per-(epoch, validator-pubkey) MEV revenue.

        Read-only view of the accumulator updated by ``_credit_validator_revenue``
        on every leader slot. Three views derive from this single map:

        * Per validator per epoch: ``view[epoch][pubkey]``.
        * Aggregate Jito-Solana validator revenue per epoch:
          ``sum(e.validator_revenue_lamports for e in view[epoch].values()
          if e.client == "jito_solana")``.
        * JitoSOL stake-pool inflow per epoch:
          ``sum(e.stake_pool_revenue_lamports for e in view[epoch].values())``.
        """
        return self._validator_revenue_by_epoch

    @property
    def priority_fee_market(self) -> Any:
        """PRD US-010 line 744: expose the execution model's priority fee
        market so agents can read it from the ``DecisionContext``. Returns
        ``None`` when the active execution model is not Solana-aware.
        """
        return getattr(self._execution_model, "priority_fee_market", None)

    def register_oracle(self, oracle: Any) -> None:
        """Attach a Pull or Push oracle so the engine can drive PRD US-006
        line 494-496 telemetry: per-slot oracle cost aggregation surfaced
        in ``SimulationResult.metadata`` and automatic ``OracleStaleEvent``
        emission when staleness exceeds tolerance.

        Pull oracles are bucketed by ``oracle_id``; their
        ``OracleUpdateAction``s admitted during a slot get counted into the
        ``_oracle_pull_slots`` ledger that ``oracle_costs_per_slot``
        consumes. Push oracles are tracked for operator-cost reporting at
        cadence boundaries.
        """
        update_mode = getattr(oracle, "update_mode", None)
        oracle_id = getattr(oracle, "oracle_id", None)
        if not oracle_id:
            raise ValueError("oracle must define a non-empty oracle_id")
        if update_mode == "pull":
            self._pull_oracles[oracle_id] = oracle
            self._oracle_pull_slots.setdefault(oracle_id, [])
        elif update_mode == "push":
            self._push_oracles[oracle_id] = oracle
        else:
            raise ValueError(
                f"oracle update_mode must be 'pull' or 'push', got {update_mode!r}"
            )

    @property
    def is_complete(self) -> bool:
        self._refresh_cancelled()
        return self._current_round >= self._config.num_rounds or self._stopped_early or self._cancelled

    def run(self) -> SimulationResult:
        """Run all rounds to completion."""
        self._ensure_started()
        while not self.is_complete:
            try:
                self.step()
            except StopIteration:
                break

        result = self._build_result()
        self._bus.emit(Event(
            type=EventType.SIMULATION_END,
            round=self._current_round,
            timestamp=self._clock.timestamp(self._current_round),
            data={"result": result},
        ))
        return result

    def step(self) -> RoundSnapshot:
        """Advance exactly one round. Raises StopIteration when complete."""
        if self.is_complete:
            raise StopIteration("Simulation is complete")

        self._ensure_started()
        if self._refresh_cancelled():
            raise StopIteration("Simulation was cancelled")
        self._current_round += 1
        snap = self._execute_round(self._current_round)
        self._record_round(snap)

        if self._config.early_stop and self._config.early_stop(snap):
            self._stopped_early = True
            if self._config.stop_reason_fn:
                self._stop_reason = self._config.stop_reason_fn(snap)

        return snap

    def _refresh_cancelled(self) -> bool:
        token = self._config.cancel_token
        if self._cancelled or token is None or not token.is_cancelled():
            return self._cancelled
        self._cancelled = True
        self._stop_reason = token.reason or self._stop_reason or "cancelled"
        return True

    def _execute_round(self, round_num: int) -> RoundSnapshot:
        """Execute a single round through all phases."""
        config = self._config
        ts = self._clock.timestamp(round_num)
        epoch = self._clock.epoch(round_num)

        # PRD US-004 line 368: rotate per-agent drop buffers so the
        # DecisionContext built this slot exposes drops from the *previous*
        # slot, not this slot's own (which haven't happened yet).
        self._last_drop_reasons_by_agent = self._next_drop_reasons_by_agent
        self._next_drop_reasons_by_agent = {}

        # Phase 1: PARAMETER & TIME OPS
        applied = self._parameters.apply_pending(round_num)
        for change, old_value in applied:
            self._bus.emit(Event(
                type=EventType.PARAMETER_CHANGED,
                round=round_num, timestamp=ts,
                data={
                    "key": change.key,
                    "old_value": old_value,
                    "new_value": change.value,
                    "source": "scheduled",
                    "proposal_id": change.proposal_id,
                    "proposed_by": change.proposed_by,
                },
            ))

        # Interest accrual / funding for single-market mode
        if not self._is_world:
            self._do_time_ops(self._market, round_num, ts)
        else:
            for name, mkt in self._market.markets.items():
                self._do_time_ops(mkt, round_num, ts)

        # Epoch boundary
        if self._clock.epoch(round_num) != self._clock.epoch(round_num - 1):
            self._bus.emit(Event(
                type=EventType.EPOCH_BOUNDARY,
                round=round_num, timestamp=ts,
                data={"epoch": epoch, "prev_epoch": self._clock.epoch(round_num - 1)},
            ))
            self._advance_lst_rates(round_num, ts, epoch)

        self._round_feed_prices = self._compute_round_feed_prices(round_num)
        if self._round_feed_prices:
            if self._last_feed_prices != self._round_feed_prices:
                self._bus.emit(Event(
                    type=EventType.ORACLE_UPDATE,
                    round=round_num,
                    timestamp=ts,
                    data={"prices": dict(self._round_feed_prices)},
                ))
            self._last_feed_prices = dict(self._round_feed_prices)

        # Phase 2: TRADING
        self._bus.emit(Event(type=EventType.ROUND_START, round=round_num, timestamp=ts, data={"epoch": epoch}))

        # Collect actions once, then route deferred protocol phases from that set.
        all_actions: list[Action] = []
        liquidation_actions: list[Action] = []
        invalid_world_liquidations: list[LiquidateAction] = []
        seen_actions: list[Action] = []
        for agent in self._agents:
            ctx = self._build_context(
                agent,
                round_num,
                ts,
                epoch,
                pending_actions=[action for action in seen_actions if getattr(action, "agent_id", None) != agent.agent_id],
            )
            actions = list(agent.decide(ctx))
            # PRD US-004 line 368: opt-in re-submission. For each drop reason
            # the agent reported in the previous slot, ask the agent whether
            # to re-submit; if so, prepend the dropped actions to this slot's
            # decisions. Default ``Agent.should_resubmit_on_drop`` returns
            # False so this is a no-op for noise/baseline agents.
            if ctx.last_drop_reasons:
                resubmissions: list[Action] = []
                for reason, dropped_actions in ctx.last_drop_reasons.items():
                    if agent.should_resubmit_on_drop(reason):
                        resubmissions.extend(dropped_actions)
                if resubmissions:
                    actions = resubmissions + actions
            seen_actions.extend(actions)
            for action in actions:
                if isinstance(action, LiquidateAction):
                    if self._is_world:
                        invalid_world_liquidations.append(action)
                    else:
                        liquidation_actions.append(action)
                    continue
                if isinstance(action, MultiMarketAction) and isinstance(action.inner, LiquidateAction):
                    if self._is_world:
                        liquidation_actions.append(action)
                    else:
                        all_actions.append(action)
                    continue
                all_actions.append(action)

        slot_pipeline = self._execution_model.supports_slot_execution()

        num_actions = 0
        trading_by_market: dict[str, list[MultiMarketAction]] = {}
        lp_by_market: dict[str, list[MultiMarketAction]] = {}
        lp_actions: list[_PlannedAction] = []

        if slot_pipeline:
            # PRD 1.0 phase-bucket preservation: a single execute_slot() call
            # per round. The engine validates MultiMarketAction wrappers and
            # passes the validated union (trading + LP) to the model. The
            # model runs admit → order on the union (so admission and global
            # ordering match legacy semantics), splits internally via
            # is_lp_action, executes trading via the executor callback,
            # invokes the run_liquidations callback below for the LIQUIDATION
            # phase, then executes LP. on_slot_end fires once after both.
            slot_pending: list[Action] = []
            if self._is_world:
                for action in all_actions:
                    if not isinstance(action, MultiMarketAction):
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            "World mode requires MultiMarketAction wrappers",
                        )
                        continue
                    if action.market_name not in self._market.markets:
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            f"Unknown market: {action.market_name}",
                        )
                        continue
                    slot_pending.append(action)
            else:
                slot_pending = list(all_actions)
            # PRD US-013: a sandwich bundle subsumes the victim sig (the
            # searcher includes a verbatim copy of the victim swap inside
            # the bundle). Drop those declared-consumed actions from the
            # regular queue here so they don't double-execute. Identity
            # match by ``is`` — the searcher's ``pending_actions`` view
            # holds the same Action objects the engine collected.
            pending_bundles = getattr(self._execution_model, "_pending_bundles", None)
            if pending_bundles:
                consumed_ids: set[int] = set()
                for b in pending_bundles:
                    for a in b.consumed_actions:
                        consumed_ids.add(id(a))
                if consumed_ids:
                    slot_pending = [a for a in slot_pending if id(a) not in consumed_ids]
            # PRD US-002 line 128 + line 167: deferred actions from prior
            # slots head the queue so re-admission honours their original
            # arrival order ahead of fresh agent submissions.
            if self._deferred_carryover:
                slot_pending = self._deferred_carryover + slot_pending
                self._deferred_carryover = []

            def run_liquidations() -> None:
                if not self._is_world:
                    self._do_liquidations(self._market, liquidation_actions, round_num, ts)
                    return
                liquidation_by_market: dict[str, list[Action]] = {}
                for invalid in invalid_world_liquidations:
                    self._emit_action_failed(
                        invalid,
                        round_num,
                        ts,
                        "World mode requires MultiMarketAction wrappers",
                    )
                for action in liquidation_actions:
                    if not isinstance(action, MultiMarketAction):
                        continue
                    if action.market_name not in self._market.markets:
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            f"Unknown market: {action.market_name}",
                        )
                        continue
                    liquidation_by_market.setdefault(action.market_name, []).append(action)
                for name, mkt in self._market.markets.items():
                    self._do_liquidations(mkt, liquidation_by_market.get(name, []), round_num, ts)

            executor = self._action_executor_for_slot(round_num, ts)
            slot_event = None
            if isinstance(self._clock, SolanaSlotClock):
                slot_event = self._clock.tick()
            ctx = SlotContext(
                slot=round_num,
                pending_actions=slot_pending,
                ordering_context=self._build_ordering_context(),
                executor=executor,
                emit=self._bus.emit,
                run_liquidations=run_liquidations,
                slot_event=slot_event,
                resolve_locks=self._resolve_action_locks,
                execute_bundle=self._execute_bundle_for_ctx(round_num, ts),
            )
            # PRD US-014 line 1101: when the execution model carries a
            # ``BlockhashHistory``, record this slot's blockhash before
            # admit so actions whose ``recent_blockhash`` references the
            # current slot validate against a populated window. The
            # blockhash payload is synthetic (slot-derived) — Phase 1's
            # blockhash mechanism is concerned with expiry semantics, not
            # the cryptographic hash itself.
            history = getattr(self._execution_model, "_blockhash_history", None)
            if history is not None:
                history.record(round_num, f"bh-{round_num}")
            # PRD US-014 line 1119: take a pre-slot snapshot when the
            # execution model carries a ``ChainReorgForkSpec`` so a fork at this slot
            # (or a future slot within ``max_reorg_depth_slots``) can revert
            # this slot's state mutations. Snapshot taken AFTER
            # ``agent.decide`` (decisions stay made) but BEFORE
            # ``execute_slot`` (action execution can be rolled back).
            fork_spec = getattr(self._execution_model, "_fork_spec", None)
            fork_events_captured: list[Event] = []
            on_fork_reorg = None
            if fork_spec is not None:
                self._fork_state_snapshots.append(
                    (round_num, self._snapshot_bundle_mutable_state())
                )
                while (
                    len(self._fork_state_snapshots)
                    > fork_spec.max_reorg_depth_slots + 1
                ):
                    self._fork_state_snapshots.popleft()

                def on_fork_reorg(event: Event) -> None:
                    fork_events_captured.append(event)

                self._bus.on(EventType.FORK_REORG, on_fork_reorg)
            try:
                outcome = self._execution_model.execute_slot(ctx)
            finally:
                if on_fork_reorg is not None:
                    self._bus.off(EventType.FORK_REORG, on_fork_reorg)
            # PRD US-014 line 1120: track this slot's admitted regular
            # actions so a future fork can replay them in the next slot.
            # Jito ``Bundle`` objects are not in ``SlotOutcome.admitted``;
            # the similarly named core ``BundleAction`` is just a normal
            # weighted basket trade and must replay like every other action.
            if fork_spec is not None:
                self._fork_admitted_actions.append((
                    round_num,
                    list(outcome.admitted),
                ))
                while (
                    len(self._fork_admitted_actions)
                    > fork_spec.max_reorg_depth_slots + 1
                ):
                    self._fork_admitted_actions.popleft()
            # PRD US-014 line 1119: on a fork hit at slot ``N`` with depth
            # ``d``, restore from the snapshot taken at slot ``N - d`` so
            # state transitions for the abandoned range ``[N - d, N]`` are
            # reverted. PRD line 1120: replay admitted non-bundle actions
            # from the abandoned slots in original order in the next slot
            # by queuing them onto ``_deferred_carryover``; bundles do
            # NOT replay.
            if fork_events_captured:
                latest = fork_events_captured[-1]
                depth = int(latest.data.get("depth", 0))
                target_slot = round_num - depth
                snapshot_to_restore: dict[str, Any] | None = None
                for snap_slot, snap_data in self._fork_state_snapshots:
                    if snap_slot == target_slot:
                        snapshot_to_restore = snap_data
                        break
                if snapshot_to_restore is None:
                    for snap_slot, snap_data in self._fork_state_snapshots:
                        if target_slot <= snap_slot <= round_num:
                            snapshot_to_restore = snap_data
                            break
                if snapshot_to_restore is not None:
                    # PRD US-014 line 1124: ``BundleTipPaid`` events for
                    # slots in ``[target_slot, round_num - 1]`` were
                    # already emitted to the bus in earlier loop
                    # iterations. Restoring the snapshot truncates
                    # ``_tip_outcomes`` but cannot retract bus events.
                    # Diff pre/post and emit a ``BundleTipReverted`` for
                    # each abandoned tip so consumers aggregating events
                    # can debit ghost tips.
                    pre_tip_outcomes = list(self._tip_outcomes)
                    self._restore_bundle_mutable_state(snapshot_to_restore)
                    survivors_set = {id(p) for p in self._tip_outcomes}
                    for original in pre_tip_outcomes:
                        if id(original) in survivors_set:
                            continue
                        if original.slot < target_slot or original.slot >= round_num:
                            continue
                        self._bus.emit(Event(
                            type=EventType.BUNDLE_TIP_REVERTED,
                            round=round_num,
                            timestamp=ts,
                            data={
                                "bundle_tip_reverted": BundleTipRevertedEvent(
                                    fork_point_slot=round_num,
                                    reorg_depth=depth,
                                    original=original,
                                ),
                            },
                        ))
                replay: list[Action] = []
                kept: deque[tuple[int, list[Action]]] = deque()
                for slot, actions in self._fork_admitted_actions:
                    if target_slot <= slot <= round_num:
                        replay.extend(actions)
                    else:
                        kept.append((slot, actions))
                self._fork_admitted_actions = kept
                if replay:
                    self._deferred_carryover = replay + self._deferred_carryover
            # PRD US-011 line 839: emit ``BundleTipPaid`` and append a
            # tip-outcomes ledger entry for each bundle that landed in
            # this slot. Reverted bundles are skipped here — including
            # bundles the fork-roll above just marked reverted via
            # ``_last_slot_selected_bundles[..].reverted``. The ledger is
            # snapshotted by ``_snapshot_bundle_mutable_state`` so atomic
            # rollbacks (US-005) and past-slot fork reverts (US-014 line
            # 1124) drop the corresponding entries automatically.
            self._emit_bundle_tip_outcomes(round_num, ts)
            self._credit_validator_revenue(round_num)
            for dropped_action, reason in outcome.dropped:
                self._emit_action_dropped(dropped_action, round_num, ts, reason)
            self._record_oracle_pulls_and_emit_stale(outcome.executed, round_num, ts)
            num_actions += len(outcome.executed)
            # PRD US-002 line 128 + line 167: per-slot CU overflow actions
            # carry into the next slot's ``pending_actions``. Re-admission
            # in the next slot re-checks blockhash expiry, so stale
            # deferrals naturally fall off rather than accumulating.
            if outcome.deferred:
                self._deferred_carryover.extend(outcome.deferred)
        else:
            admitted_actions, dropped_actions = self._execution_model.admit(
                all_actions,
                round_num,
                self._build_ordering_context(),
            )
            for dropped_action, reason in dropped_actions:
                self._emit_action_dropped(dropped_action, round_num, ts, reason)

            if self._is_world:
                for action in admitted_actions:
                    if not isinstance(action, MultiMarketAction):
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            "World mode requires MultiMarketAction wrappers",
                        )
                        continue
                    if action.market_name not in self._market.markets:
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            f"Unknown market: {action.market_name}",
                        )
                        continue
                    bucket = lp_by_market if isinstance(action.inner, LPAction) else trading_by_market
                    bucket.setdefault(action.market_name, []).append(action)

                for market_name, actions in trading_by_market.items():
                    ordering_context = self._build_ordering_context(market_name)
                    ordered_actions = self._execution_model.order(actions, round_num, ordering_context)
                    planned_actions = self._plan_actions(ordered_actions, round_num, ts)
                    for planned in planned_actions:
                        self._execute_action(
                            planned.action,
                            round_num,
                            ts,
                            execution_cost=planned.execution_cost,
                            cost_token=planned.cost_token,
                        )
                        num_actions += 1
            else:
                ordered_actions = self._execution_model.order(admitted_actions, round_num, self._build_ordering_context())
                planned_actions = self._plan_actions(ordered_actions, round_num, ts)
                trading_actions = [planned for planned in planned_actions if not isinstance(planned.action, LPAction)]
                lp_actions = [planned for planned in planned_actions if isinstance(planned.action, LPAction)]

                for planned in trading_actions:
                    self._execute_action(
                        planned.action,
                        round_num,
                        ts,
                        execution_cost=planned.execution_cost,
                        cost_token=planned.cost_token,
                    )
                    num_actions += 1

        # Phase 3 (LIQUIDATION) and Phase 4 (LP) for slot-aware models are
        # driven from inside execute_slot(): the model invokes the engine's
        # run_liquidations callback between trading and LP execution.
        if not slot_pipeline:
            # Phase 3: LIQUIDATION
            if not self._is_world:
                self._do_liquidations(self._market, liquidation_actions, round_num, ts)
            else:
                liquidation_by_market: dict[str, list[Action]] = {}
                for action in invalid_world_liquidations:
                    self._emit_action_failed(
                        action,
                        round_num,
                        ts,
                        "World mode requires MultiMarketAction wrappers",
                    )
                for action in liquidation_actions:
                    if not isinstance(action, MultiMarketAction):
                        continue
                    if action.market_name not in self._market.markets:
                        self._emit_action_failed(
                            action,
                            round_num,
                            ts,
                            f"Unknown market: {action.market_name}",
                        )
                        continue
                    liquidation_by_market.setdefault(action.market_name, []).append(action)
                for name, mkt in self._market.markets.items():
                    self._do_liquidations(mkt, liquidation_by_market.get(name, []), round_num, ts)

            # Phase 4: LP OPERATIONS
            if self._is_world:
                for market_name, actions in lp_by_market.items():
                    ordering_context = self._build_ordering_context(market_name)
                    ordered_actions = self._execution_model.order(actions, round_num, ordering_context)
                    planned_actions = self._plan_actions(ordered_actions, round_num, ts)
                    for planned in planned_actions:
                        self._execute_action(
                            planned.action,
                            round_num,
                            ts,
                            execution_cost=planned.execution_cost,
                            cost_token=planned.cost_token,
                        )
                        num_actions += 1
            else:
                for planned in lp_actions:
                    self._execute_action(
                        planned.action,
                        round_num,
                        ts,
                        execution_cost=planned.execution_cost,
                        cost_token=planned.cost_token,
                    )
                    num_actions += 1

            self._execution_model.on_round_end(num_actions, round_num)

        # Phase 5: FEE ATTRIBUTION
        if not self._is_world:
            self._do_fee_attribution(self._market, round_num, ts)
        else:
            for name, mkt in self._market.markets.items():
                self._do_fee_attribution(mkt, round_num, ts)

        # Phase 6: INCENTIVES
        if config.emission_schedule and config.reward_distributor:
            prev_ts = self._clock.timestamp(round_num - 1) if round_num > 1 else self._clock.timestamp(0)
            rewards = config.emission_schedule.rewards_for_period(prev_ts, ts)
            if rewards:
                markets_list = list(self._market.markets.values()) if self._is_world else [self._market]
                agent_states = {a.agent_id: a.state for a in self._agents}
                deltas = config.reward_distributor.distribute(rewards, markets_list, agent_states)
                for agent_id, token_deltas in deltas.items():
                    for agent in self._agents:
                        if agent.agent_id == agent_id:
                            for token, amount in token_deltas.items():
                                agent.state.balances[token] = agent.state.balances.get(token, 0) + amount
                            break
                self._bus.emit(Event(
                    type=EventType.REWARD_DISTRIBUTED,
                    round=round_num, timestamp=ts,
                    data={
                        "token": next(iter(rewards)) if len(rewards) == 1 else None,
                        "total": next(iter(rewards.values())) if len(rewards) == 1 else sum(float(v) for v in rewards.values()),
                        "rewards": rewards,
                        "per_agent": (
                            {agent_id: token_map[next(iter(rewards))] for agent_id, token_map in deltas.items()}
                            if len(rewards) == 1 else deltas
                        ),
                    },
                ))

        # Phase 7: OBSERVE & RECORD
        snap = self._build_snapshot(round_num, ts, epoch)
        for agent in self._agents:
            agent.on_round_end(round_num, snap)

        self._bus.emit(Event(
            type=EventType.ROUND_END,
            round=round_num, timestamp=ts,
            data={"market_state": snap.market_state, "all_market_states": snap.all_market_states},
        ))

        if isinstance(self._info_filter, DelayedInformation):
            recorded_state: MarketSnapshot | dict[str, MarketSnapshot] | None = snap.market_state
            if self._is_world:
                recorded_state = snap.all_market_states
            if recorded_state is not None:
                self._info_filter.record(recorded_state, self._round_feed_prices)

        return snap

    def _ensure_started(self) -> None:
        if self._started:
            return

        self._bus.emit(Event(
            type=EventType.SIMULATION_START,
            round=0,
            timestamp=self._clock.timestamp(0),
            data={
                "market": None if self._is_world else self._market.get_state(),
                "all_markets": self._market.get_all_states() if self._is_world else None,
                "num_rounds": self._config.num_rounds,
                "agents": [a.agent_id for a in self._agents],
            },
        ))

        for change, old_value in self._parameters.apply_pending(0):
            self._bus.emit(Event(
                type=EventType.PARAMETER_CHANGED,
                round=0,
                timestamp=self._clock.timestamp(0),
                data={
                    "key": change.key,
                    "old_value": old_value,
                    "new_value": change.value,
                    "source": "scheduled",
                    "proposal_id": change.proposal_id,
                    "proposed_by": change.proposed_by,
                },
            ))

        self._started = True

    def _record_round(self, snap: RoundSnapshot) -> None:
        prices = self._collect_round_prices()
        if prices is not None:
            self._price_history.append(prices)

        self._fee_history.append(
            {dest: dict(tokens) for dest, tokens in self._round_fee_splits.items()}
        )
        self._round_fee_splits = {}

        if snap.round % self._config.snapshot_interval == 0:
            if self._config.snapshot_callback:
                self._config.snapshot_callback(copy.deepcopy(snap))
            if self._config.retain_snapshots:
                self._snapshots.append(copy.deepcopy(snap))

        if self._config.progress_callback:
            self._config.progress_callback(snap.round, self._config.num_rounds)

    def _build_result(self) -> SimulationResult:
        agent_final = {a.agent_id: copy.deepcopy(a.state) for a in self._agents}
        metadata: dict[str, Any] = {
            "fee_destination_balances": copy.deepcopy(self._fee_destination_balances),
            "parameter_state": self._parameters.to_dict(),
        }
        # PRD US-004 line 369: surface submission-path priors (and their
        # calibrated_at marker) so consumers see synthetic vs calibrated.
        priors = getattr(self._execution_model, "_submission_priors", None)
        if priors is not None:
            metadata["submission_priors"] = {
                "rpc_landing_prob_baseline": priors.rpc_landing_prob_baseline,
                "tpu_quic_landing_prob_baseline": priors.tpu_quic_landing_prob_baseline,
                "jito_relayer_landing_prob_baseline": priors.jito_relayer_landing_prob_baseline,
                "congestion_penalty_per_pct_full": priors.congestion_penalty_per_pct_full,
                "calibrated_at": priors.calibrated_at,
            }
            metadata["priors_calibrated_at"] = (
                priors.calibrated_at if priors.calibrated_at is not None else "synthetic"
            )
        elif isinstance(self._execution_model, SolanaLikeExecution):
            # PRD US-004 line 383: every solana_like run surfaces the
            # calibration marker so consumers (UI badge, exports) can
            # tell synthetic-context runs from calibrated ones, even when
            # no explicit ``submission_priors`` were supplied.
            metadata["submission_priors"] = {"calibrated_at": None}
            metadata["priors_calibrated_at"] = "synthetic"
        # PRD US-006 line 496: surface per-slot oracle update costs (CU,
        # consumer-paid lamports, push-operator lamports). Only emitted
        # when at least one oracle is registered so non-Solana runs stay
        # byte-for-byte identical.
        if self._pull_oracles or self._push_oracles:
            from defi_sim.engine.oracles.metrics import oracle_costs_per_slot
            push_window: tuple[int, int] | None = None
            if self._push_oracles and self._current_round > 0:
                push_window = (0, self._current_round)
            slot_costs = oracle_costs_per_slot(
                pull_oracle_pulls=self._oracle_pull_slots,
                pull_oracles=self._pull_oracles,
                push_oracles=self._push_oracles or None,
                push_slot_window=push_window,
            )
            metadata["oracle_costs_per_slot"] = [
                {
                    "slot": entry.slot,
                    "cu": entry.cu,
                    "lamports": entry.lamports,
                    "operator_lamports": entry.operator_lamports,
                }
                for entry in slot_costs
            ]
        derived = self._compute_derived_metrics(agent_final)
        if derived:
            metadata["derived_metrics"] = derived
        return SimulationResult(
            price_history=list(self._price_history),
            fee_history=[
                {dest: dict(tokens) for dest, tokens in splits.items()}
                for splits in self._fee_history
            ],
            agent_final_states=agent_final,
            round_snapshots=list(self._snapshots),
            num_rounds=self._config.num_rounds,
            num_rounds_executed=self._current_round,
            seed=self._config.seed,
            stopped_early=self._stopped_early or self._cancelled,
            cancelled=self._cancelled,
            stop_reason=self._stop_reason,
            metadata=metadata,
        )

    def _compute_derived_metrics(
        self,
        agent_final: dict[AgentId, AgentState],
    ) -> dict[str, float | None]:
        """Compute end-of-run summary metrics that aren't already in result fields.

        Returns a dict with keys:
            - kl_divergence: KL of binned log-returns vs. a Gaussian fit to the
              same series (measures non-normality of price action). None when
              fewer than 2 prices or zero variance.
            - convergence_speed: round at which a 5-round rolling stdev of log-
              returns first drops below 0.005. None when fewer than 6 returns.
            - manipulation_cost: total Jito tip lamports paid divided by the
              absolute end-to-end price move of the primary token.
              float('inf') when price didn't move; None when no tips were paid.
            - slippage: cost of a 1% test trade in the primary token against
              the post-run market state (fraction). None when no priced market
              or unpriced primary token.
            - exitability: fraction of any JitoSearcher's primary-token
              holdings that can be liquidated against the final market.
              None when there's no JitoSearcher or no holdings.

        All values are best-effort and defensive: any error in a single
        sub-computation produces ``None`` for that key without failing the run.
        """
        from defi_sim.agents.jito_searcher import JitoSearcher
        from defi_sim.metrics.generic import (
            compute_slippage,
            convergence_speed_revised,
            exitability as exitability_fn,
            kl_divergence,
            manipulation_cost,
        )

        out: dict[str, float | None] = {
            "kl_divergence": None,
            "convergence_speed": None,
            "manipulation_cost": None,
            "slippage": None,
            "exitability": None,
        }

        # Concentrated-LP metrics: in-range fraction, range-bounded IL, and
        # fees-vs-IL break-even ratio. Computed per-LP from the Whirlpool
        # market's surviving and historic position records.
        try:
            self._populate_clmm_lp_metrics(out)
        except Exception:
            pass

        last_prices: dict[TokenId, Numeric] = (
            self._price_history[-1] if self._price_history else {}
        )
        primary_token: TokenId | None = None
        quote_token: TokenId | None = None
        for tok, price in last_prices.items():
            try:
                p = float(price)
            except (TypeError, ValueError):
                continue
            if abs(p - 1.0) < 0.01 and quote_token is None:
                quote_token = tok
            elif primary_token is None:
                primary_token = tok
        if primary_token is None and last_prices:
            primary_token = next(iter(last_prices.keys()))

        primary_series: list[float] = []
        if primary_token is not None:
            for snap in self._price_history:
                val = snap.get(primary_token) if isinstance(snap, dict) else None
                try:
                    primary_series.append(float(val))
                except (TypeError, ValueError):
                    continue

        log_returns: list[float] = []
        if len(primary_series) >= 2:
            for prev, curr in zip(primary_series[:-1], primary_series[1:]):
                if prev > 0 and curr > 0:
                    log_returns.append(float(np.log(curr / prev)))

        if len(log_returns) >= 2:
            arr = np.asarray(log_returns, dtype=float)
            sigma = float(arr.std())
            if sigma > 0:
                bins = 20
                lo, hi = float(arr.min()), float(arr.max())
                if hi > lo:
                    edges = np.linspace(lo, hi, bins + 1)
                    counts, _ = np.histogram(arr, bins=edges)
                    p = counts.astype(float) / counts.sum()
                    mu = float(arr.mean())
                    centers = 0.5 * (edges[:-1] + edges[1:])
                    width = edges[1] - edges[0]
                    pdf = (
                        np.exp(-0.5 * ((centers - mu) / sigma) ** 2)
                        / (sigma * np.sqrt(2 * np.pi))
                    )
                    q = pdf * width
                    q_sum = q.sum()
                    if q_sum > 0:
                        q = q / q_sum
                        try:
                            kl = kl_divergence(p.tolist(), q.tolist())
                            if np.isfinite(kl):
                                out["kl_divergence"] = float(kl)
                        except Exception:
                            pass

        if len(log_returns) >= 6:
            window = 5
            roll_vol: list[float] = []
            for i in range(len(log_returns) - window + 1):
                roll_vol.append(float(np.std(log_returns[i:i + window])))
            try:
                cs = convergence_speed_revised(roll_vol, threshold=0.005, window=window)
                out["convergence_speed"] = float(cs)
            except Exception:
                pass

        total_tip_lamports = 0
        for outcome in self._tip_outcomes:
            try:
                total_tip_lamports += int(outcome.tip_lamports)
            except (TypeError, ValueError, AttributeError):
                continue
        if total_tip_lamports > 0 and len(primary_series) >= 2:
            move = abs(primary_series[-1] - primary_series[0])
            try:
                mc = float(manipulation_cost(total_tip_lamports, move))
                if np.isfinite(mc):
                    out["manipulation_cost"] = mc
            except Exception:
                pass

        priced_market: PricedMarket | None = None
        if isinstance(self._market, PricedMarket):
            priced_market = self._market
        elif self._is_world:
            for sub in self._market.markets.values():
                if isinstance(sub, PricedMarket):
                    priced_market = sub
                    break

        if priced_market is not None and primary_token is not None:
            try:
                slip = compute_slippage(priced_market, primary_token, trade_fraction=0.01)
                out["slippage"] = float(slip)
            except Exception:
                pass

            searcher_holdings: dict[TokenId, Numeric] | None = None
            for agent in self._agents:
                if isinstance(agent, JitoSearcher):
                    state = agent_final.get(agent.agent_id)
                    if state is None:
                        continue
                    bal = state.balances.get(primary_token, 0)
                    try:
                        amount = float(bal)
                    except (TypeError, ValueError):
                        amount = 0.0
                    if amount > 0:
                        searcher_holdings = {primary_token: amount}
                        break
            if searcher_holdings is not None:
                try:
                    ex = exitability_fn(priced_market, searcher_holdings)
                    out["exitability"] = float(ex)
                except Exception:
                    pass

        return out

    def _populate_clmm_lp_metrics(self, out: dict[str, float | None]) -> None:
        """Fill the three range-aware LP metrics into ``out``.

        For each Whirlpool LP position still alive at run end, emits:
            - lp_in_range_fraction:<agent_id>
            - range_il:<agent_id>
            - fees_vs_il_breakeven:<agent_id>
        Plus a pool-wide summary keyed by the bare metric name (averaged
        across positions) so a default UI surface can pick one number.
        """
        from defi_sim.markets.whirlpool import WhirlpoolMarket

        markets: list[WhirlpoolMarket] = []
        if self._is_world:
            for mkt in self._market.markets.values():
                if isinstance(mkt, WhirlpoolMarket):
                    markets.append(mkt)
        elif isinstance(self._market, WhirlpoolMarket):
            markets.append(self._market)
        if not markets:
            return

        # Total swap volume in quote-token (token B) human units across
        # all CLMM markets. Surfaces directly so dashboards can contrast
        # volume between fee-tier runs without reaching into per-round
        # snapshots. ``token_decimals_b`` may differ across pools in a
        # world spec; we sum human-unit volume so the metric stays
        # additive.
        total_volume_b_human = 0.0
        for market in markets:
            decimals_b = int(market.pool.token_decimals_b)
            total_volume_b_human += market._total_volume_b_raw / (10 ** decimals_b)
        if total_volume_b_human > 0:
            out["total_volume_quote"] = total_volume_b_human

        in_range_fractions: list[float] = []
        range_ils: list[float] = []
        breakevens: list[float] = []
        fees_per_l: list[float] = []

        for market in markets:
            for pos in market.all_position_records():
                # Per-position metrics keyed by agent_id so the demo can
                # contrast LP variants (e.g. lp-passive vs lp-rebalancing).
                if pos.total_rounds > 0:
                    frac = pos.in_range_rounds / pos.total_rounds
                    out[f"lp_in_range_fraction:{pos.agent_id}"] = float(frac)
                    in_range_fractions.append(frac)

                value_now = market.position_value_in_b(pos)
                value_hodl = market.hodl_value_in_b(pos)
                if value_hodl > 0:
                    il = max(0.0, 1.0 - float(value_now) / float(value_hodl))
                    out[f"range_il:{pos.agent_id}"] = il
                    range_ils.append(il)

                    # fees-vs-IL break-even: collect any pending fees so
                    # the comparison reflects everything earned, then
                    # divide quote-denominated fees by IL in quote units.
                    market._collect_fees_into_position(pos)
                    sqrt_p = market.pool.sqrt_price_x64
                    fees_a_in_b = (
                        pos.accumulated_fees_a * sqrt_p * sqrt_p
                    ) // (1 << 128)
                    fees_in_b = int(pos.accumulated_fees_b + fees_a_in_b)
                    il_in_b = max(0.0, float(value_hodl) - float(value_now))
                    if il_in_b > 0:
                        breakeven = float(fees_in_b) / il_in_b
                    elif fees_in_b > 0:
                        breakeven = float("inf")
                    else:
                        breakeven = 0.0
                    out[f"fees_vs_il_breakeven:{pos.agent_id}"] = breakeven
                    breakevens.append(breakeven)

                    # Fees normalized by the position's HODL-marked notional
                    # (deposited tokens marked-to-market in quote units).
                    # Both numerator and denominator are raw token-B units,
                    # so the ratio is dimensionless and renders as "fees as
                    # a fraction of LP notional" — a fee-yield-style number
                    # sized 1e-4 to 5e-2 over a typical run, which displays
                    # cleanly as a percent. Raw L was the wrong denominator:
                    # it made the ratio ~1e-13 and rounded to zero in the UI.
                    if pos.liquidity > 0 and value_hodl > 0:
                        fpl = float(fees_in_b) / float(value_hodl)
                        out[f"lp_fees_per_liquidity:{pos.agent_id}"] = fpl
                        fees_per_l.append(fpl)

        if in_range_fractions:
            out["lp_in_range_fraction"] = float(
                sum(in_range_fractions) / len(in_range_fractions)
            )
        if range_ils:
            out["range_il"] = float(sum(range_ils) / len(range_ils))
        if breakevens:
            finite = [b for b in breakevens if b != float("inf")]
            if finite:
                out["fees_vs_il_breakeven"] = float(sum(finite) / len(finite))
            else:
                out["fees_vs_il_breakeven"] = float("inf")
        if fees_per_l:
            out["lp_fees_per_liquidity"] = float(
                sum(fees_per_l) / len(fees_per_l)
            )

    def _execute_action(
        self,
        action: Action,
        round_num: int,
        ts: int,
        market_override: Market | None = None,
        *,
        execution_cost: Numeric = 0,
        cost_token: TokenId | None = None,
    ) -> None:
        """Execute a single action after planning has reserved its execution cost."""
        # Handle MultiMarketAction
        if isinstance(action, MultiMarketAction):
            if not self._is_world:
                self._emit_action_failed(action, round_num, ts, "MultiMarketAction requires World mode")
                return
            mkt = self._market.markets.get(action.market_name)
            if mkt is None:
                self._emit_action_failed(action, round_num, ts, f"Unknown market: {action.market_name}")
                return
            self._execute_action(
                action.inner,
                round_num,
                ts,
                market_override=mkt,
                execution_cost=execution_cost,
                cost_token=cost_token,
            )
            return

        # Handle ConditionalAction
        if isinstance(action, ConditionalAction):
            if action.predicate is not None:
                agent = self._find_agent(action.agent_id)
                if agent is None:
                    return
                market_state = market_override.get_state() if market_override is not None else self._get_market_state()
                if not action.predicate.evaluate(market_state, agent.state):
                    self._refund_execution_cost(agent, execution_cost, cost_token)
                    return  # Silently skip
            self._execute_action(
                action.inner,
                round_num,
                ts,
                market_override=market_override,
                execution_cost=execution_cost,
                cost_token=cost_token,
            )
            return

        # Handle AtomicAction
        if isinstance(action, AtomicAction):
            self._execute_atomic(
                action,
                round_num,
                ts,
                market_override=market_override,
                execution_cost=execution_cost,
                cost_token=cost_token,
            )
            return

        # Handle FlashLoanAction
        if isinstance(action, FlashLoanAction):
            self._execute_flash_loan(
                action,
                round_num,
                ts,
                market_override=market_override,
                execution_cost=execution_cost,
                cost_token=cost_token,
            )
            return

        # Regular action
        market = market_override
        if market is None:
            if self._is_world:
                self._emit_action_failed(action, round_num, ts, "World mode requires MultiMarketAction wrappers")
                return
            market = self._market
        if market is not None:
            self._execute_on_market(
                action,
                market,
                round_num,
                ts,
                execution_cost=execution_cost,
                cost_token=cost_token,
            )

    def _execute_on_market(
        self,
        action: Action,
        market: Market,
        round_num: int,
        ts: int,
        *,
        execution_cost: Numeric,
        cost_token: TokenId | None,
    ) -> None:
        """Execute an action on a specific market."""
        agent = self._find_agent(action.agent_id)
        if agent is None:
            return

        # Build execution context
        ctx = ExecutionContext(
            agent_state=agent.state,
            current_round=round_num,
            total_rounds=self._config.num_rounds,
            timestamp=ts,
            market_state=market.get_state(),
            numeric_mode=self._config.numeric_mode,
            default_fee_model=self._config.default_fee_model,
            execution_cost=execution_cost,
            parameters=self._parameters,
        )

        # Execute
        result = market.execute(action, ctx)

        if result.success:
            self._apply_execution_result(agent, result, market=market)
            self._route_fee_splits(result, market)

            self._record_slot_success()
            self._bus.emit(Event(
                type=EventType.ACTION_EXECUTED, round=round_num, timestamp=ts,
                data=self._action_event_data(
                    action,
                    result,
                    execution_cost=execution_cost,
                    cost_token=cost_token,
                ),
            ))
            if isinstance(action, LiquidateAction):
                seized_amount = max(result.token_deltas.get(action.seize_token, 0), 0)
                self._bus.emit(Event(
                    type=EventType.LIQUIDATION,
                    round=round_num,
                    timestamp=ts,
                    data={
                        "liquidator_id": action.agent_id,
                        "target_id": action.target_agent_id,
                        "repay_amount": action.repay_amount,
                        "seized_amount": seized_amount,
                    },
                ))
        else:
            if self._execution_model.refund_on_failure(action):
                self._refund_execution_cost(agent, execution_cost, cost_token)

            self._record_slot_failure(result.error or "execution failed")
            self._bus.emit(Event(
                type=EventType.ACTION_FAILED, round=round_num, timestamp=ts,
                data=self._action_event_data(
                    action,
                    result,
                    execution_cost=execution_cost,
                    cost_token=cost_token,
                ),
            ))

    def _execute_atomic(
        self,
        action: AtomicAction,
        round_num: int,
        ts: int,
        market_override: Market | None = None,
        *,
        execution_cost: Numeric,
        cost_token: TokenId | None,
    ) -> None:
        """Execute multiple sub-actions atomically with rollback on failure."""
        agent = self._find_agent(action.agent_id)
        if agent is None:
            return

        market = market_override if market_override is not None else (self._market if not self._is_world else None)
        if market is None:
            self._emit_action_failed(action, round_num, ts, "AtomicAction requires a target market")
            return

        agent_backups = self._snapshot_agent_states()
        market_backup = market.copy()

        merged_deltas: dict[TokenId, Numeric] = {}
        merged_other_deltas: dict[AgentId, dict[TokenId, Numeric]] = {}
        total_fees = 0
        merged_fee_splits: dict[str, Numeric] = {}
        fee_token: TokenId | None = None
        merged_volume: Numeric | None = 0
        merged_other_volumes: dict[AgentId, Numeric] = {}

        for sub_action in action.actions:
            ctx = ExecutionContext(
                agent_state=agent.state,
                current_round=round_num,
                total_rounds=self._config.num_rounds,
                timestamp=ts,
                market_state=market.get_state(),
                numeric_mode=self._config.numeric_mode,
                default_fee_model=self._config.default_fee_model,
                execution_cost=execution_cost,
                parameters=self._parameters,
            )
            result = market.execute(sub_action, ctx)

            if not result.success:
                # Rollback
                self._restore_market_reference(market, market_backup)
                self._restore_agent_states(agent_backups)
                if self._execution_model.refund_on_failure(action):
                    self._refund_execution_cost(agent, execution_cost, cost_token)
                self._emit_action_failed(
                    action,
                    round_num,
                    ts,
                    f"Atomic rollback: {result.error}",
                    execution_cost=execution_cost,
                    cost_token=cost_token,
                )
                return

            self._apply_execution_result(agent, result, market=market)
            for token_id, delta in result.token_deltas.items():
                merged_deltas[token_id] = merged_deltas.get(token_id, 0) + delta
            for other_agent_id, deltas in result.other_agent_deltas.items():
                merged = merged_other_deltas.setdefault(other_agent_id, {})
                for token_id, delta in deltas.items():
                    merged[token_id] = merged.get(token_id, 0) + delta
            total_fees += result.fees_paid
            for destination, amount in result.fee_splits.items():
                merged_fee_splits[destination] = merged_fee_splits.get(destination, 0) + amount
            if result.fee_token is not None:
                fee_token = result.fee_token
            if result.volume is not None:
                merged_volume = (merged_volume or 0) + result.volume
            else:
                merged_volume = None
            for other_agent_id, volume in result.other_agent_volumes.items():
                merged_other_volumes[other_agent_id] = merged_other_volumes.get(other_agent_id, 0) + volume

        merged_result = ExecutionResult(
            success=True,
            token_deltas=merged_deltas,
            other_agent_deltas=merged_other_deltas,
            fees_paid=total_fees,
            fee_splits=merged_fee_splits,
            fee_token=fee_token,
            volume=merged_volume,
            other_agent_volumes=merged_other_volumes,
        )
        self._route_fee_splits(merged_result, market)

        self._record_slot_success()
        self._bus.emit(Event(
            type=EventType.ACTION_EXECUTED, round=round_num, timestamp=ts,
            data=self._action_event_data(
                action,
                merged_result,
                execution_cost=execution_cost,
                cost_token=cost_token,
            ),
        ))

    def _execute_flash_loan(
        self,
        action: FlashLoanAction,
        round_num: int,
        ts: int,
        market_override: Market | None = None,
        *,
        execution_cost: Numeric,
        cost_token: TokenId | None,
    ) -> None:
        """Execute flash loan with repayment check."""
        agent = self._find_agent(action.agent_id)
        if agent is None:
            return

        market = market_override if market_override is not None else (self._market if not self._is_world else None)
        if market is None:
            self._emit_action_failed(action, round_num, ts, "FlashLoanAction requires a target market")
            return

        agent_backups = self._snapshot_agent_states()
        market_backup = market.copy()
        merged_deltas: dict[TokenId, Numeric] = {}
        merged_other_deltas: dict[AgentId, dict[TokenId, Numeric]] = {}
        total_fees: Numeric = 0
        merged_fee_splits: dict[str, Numeric] = {}
        fee_token: TokenId | None = None
        merged_volume: Numeric | None = 0
        merged_other_volumes: dict[AgentId, Numeric] = {}

        # Credit borrowed tokens
        agent.state.balances[action.token] = agent.state.balances.get(action.token, 0) + action.amount

        # Execute inner actions
        for sub_action in action.inner_actions:
            ctx = ExecutionContext(
                agent_state=agent.state,
                current_round=round_num,
                total_rounds=self._config.num_rounds,
                timestamp=ts,
                market_state=market.get_state(),
                numeric_mode=self._config.numeric_mode,
                default_fee_model=self._config.default_fee_model,
                execution_cost=execution_cost,
                parameters=self._parameters,
            )
            result = market.execute(sub_action, ctx)
            if result.success:
                self._apply_execution_result(agent, result, market=market)
                for token_id, delta in result.token_deltas.items():
                    merged_deltas[token_id] = merged_deltas.get(token_id, 0) + delta
                for other_agent_id, deltas in result.other_agent_deltas.items():
                    merged = merged_other_deltas.setdefault(other_agent_id, {})
                    for token_id, delta in deltas.items():
                        merged[token_id] = merged.get(token_id, 0) + delta
                total_fees += result.fees_paid
                for destination, amount in result.fee_splits.items():
                    merged_fee_splits[destination] = merged_fee_splits.get(destination, 0) + amount
                if result.fee_token is not None:
                    fee_token = result.fee_token
                if result.volume is not None:
                    merged_volume = (merged_volume or 0) + result.volume
                else:
                    merged_volume = None
                for other_agent_id, volume in result.other_agent_volumes.items():
                    merged_other_volumes[other_agent_id] = merged_other_volumes.get(other_agent_id, 0) + volume
            else:
                self._restore_market_reference(market, market_backup)
                self._restore_agent_states(agent_backups)
                if self._execution_model.refund_on_failure(action):
                    self._refund_execution_cost(agent, execution_cost, cost_token)
                self._emit_action_failed(
                    action,
                    round_num,
                    ts,
                    f"Flash loan inner action failed: {result.error}",
                    execution_cost=execution_cost,
                    cost_token=cost_token,
                )
                return

        # Check repayment
        current_balance = agent.state.balance(action.token)
        if current_balance < action.amount:
            # Rollback
            self._restore_market_reference(market, market_backup)
            self._restore_agent_states(agent_backups)
            if self._execution_model.refund_on_failure(action):
                self._refund_execution_cost(agent, execution_cost, cost_token)
            self._emit_action_failed(
                action,
                round_num,
                ts,
                "Flash loan repayment failed",
                execution_cost=execution_cost,
                cost_token=cost_token,
            )
            return

        # Debit repayment
        agent.state.balances[action.token] -= action.amount

        merged_result = ExecutionResult(
            success=True,
            token_deltas=merged_deltas,
            other_agent_deltas=merged_other_deltas,
            fees_paid=total_fees,
            fee_splits=merged_fee_splits,
            fee_token=fee_token,
            volume=merged_volume,
            other_agent_volumes=merged_other_volumes,
        )
        self._route_fee_splits(merged_result, market)

        self._record_slot_success()
        self._bus.emit(Event(
            type=EventType.ACTION_EXECUTED, round=round_num, timestamp=ts,
            data=self._action_event_data(
                action,
                merged_result,
                execution_cost=execution_cost,
                cost_token=cost_token,
            ),
        ))

    # --- Helper methods ---

    def _advance_lst_rates(self, round_num: int, ts: int | float, epoch: int) -> None:
        """Apply per-epoch drift to every registered LST and emit events.

        Walks ``config.lst_tokens`` (set up at engine init) and mutates
        each LST's ``exchange_rate_to_sol`` in place via ``advance_lst_rate``.
        Skips tokens missing either an exchange rate or a drift spec.
        """

        for token in (self._config.lst_tokens or []):
            drift = getattr(token, "exchange_rate_drift", None)
            if drift is None or token.exchange_rate_to_sol is None:
                continue
            rng = self._lst_rngs.get(token.id, self._engine_rng)
            new_rate, delta = advance_lst_rate(token, epoch, rng)
            self._bus.emit(Event(
                type=EventType.LST_RATE_UPDATED,
                round=round_num, timestamp=ts,
                data={
                    "epoch": epoch,
                    "token_id": token.id,
                    "new_rate": new_rate,
                    "delta": delta,
                },
            ))

    def _do_time_ops(self, market: Market, round_num: int, ts: int) -> None:
        """Interest accrual and funding settlement."""
        if round_num <= 1:
            return
        elapsed = self._clock.elapsed(round_num - 1, round_num)

        if isinstance(market, LendingMarket):
            market.accrue_interest(elapsed)
            state = market.get_state()
            rates = {
                token: market.get_interest_rate(token)
                for token in state.tokens
            }
            self._bus.emit(Event(
                type=EventType.INTEREST_ACCRUED,
                round=round_num, timestamp=ts,
                data={"elapsed_seconds": elapsed, "rates": rates},
            ))

        if isinstance(market, DerivativesMarket):
            deltas = market.settle_funding(elapsed)
            state = market.get_state()
            rates = {
                token: market.get_funding_rate(token)
                for token in state.tokens
            }
            for agent_id, amount in deltas:
                agent = self._find_agent(agent_id)
                if agent is not None:
                    settlement_token = self._resolve_market_token(
                        market,
                        ("settlement_token", "collateral_token", "_collateral_token"),
                    )
                    if settlement_token is None:
                        raise ValueError(
                            "DerivativesMarket must expose settlement_token or collateral_token"
                        )
                    agent.state.balances[settlement_token] = (
                        agent.state.balances.get(settlement_token, 0) + amount
                    )
            self._bus.emit(Event(
                type=EventType.FUNDING_SETTLED,
                round=round_num, timestamp=ts,
                data={
                    "token": state.tokens[0] if len(state.tokens) == 1 else None,
                    "rate": rates[state.tokens[0]] if len(state.tokens) == 1 else None,
                    "rates": rates,
                    "transfers": deltas,
                },
            ))

    def _do_liquidations(
        self,
        market: Market,
        liquidation_actions: list[Action],
        round_num: int,
        ts: int,
    ) -> None:
        """Execute liquidations if market supports them."""
        if not isinstance(market, Liquidatable):
            return

        targets = market.get_liquidatable_agents()
        if not targets:
            return

        collected_actions: list[Action] = []
        for action in liquidation_actions:
            if isinstance(action, LiquidateAction):
                collected_actions.append(action)
            elif (
                isinstance(action, MultiMarketAction)
                and isinstance(action.inner, LiquidateAction)
                and self._is_world
                and self._market.markets.get(action.market_name) is market
            ):
                collected_actions.append(action)

        if not collected_actions:
            return

        market_name = self._market_name_for(market)
        ordering_context = self._build_ordering_context(market_name)
        ordered_actions = self._execution_model.order(collected_actions, round_num, ordering_context)
        planned_actions = self._plan_actions(ordered_actions, round_num, ts)
        for planned in planned_actions:
            self._execute_action(
                planned.action,
                round_num,
                ts,
                market_override=market if not isinstance(planned.action, MultiMarketAction) else None,
                execution_cost=planned.execution_cost,
                cost_token=planned.cost_token,
            )

    def _do_fee_attribution(self, market: Market, round_num: int, ts: int) -> None:
        """Distribute accumulated fees to LP agents."""
        if not isinstance(market, LiquidityPool):
            return

        pending_fees = self._pending_lp_fees.pop(id(market), {})
        legacy_fees = market.reset_accumulated_fees()
        if not pending_fees and legacy_fees <= 0:
            return

        positions = market.get_all_lp_positions()
        total_shares = sum(p.share_fraction for p in positions)
        if total_shares <= 0:
            return

        if not pending_fees and legacy_fees > 0:
            fee_token = self._resolve_market_token(
                market,
                ("fee_token", "collateral_token", "_collateral_token"),
            )
            if fee_token is None:
                raise ValueError("LiquidityPool fee attribution requires an explicit fee token")
            pending_fees = {fee_token: legacy_fees}

        total_fees: Numeric = 0.0 if any(isinstance(amount, float) for amount in pending_fees.values()) else 0
        per_agent: dict[AgentId, dict[TokenId, Numeric]] = {}

        for token_id, fees in pending_fees.items():
            if fees <= 0:
                continue
            total_fees += fees
            for pos in positions:
                if isinstance(fees, float):
                    share = fees * pos.share_fraction / total_shares
                else:
                    share = fees * pos.share_fraction // total_shares
                if share <= 0:
                    continue

                agent = self._find_agent(pos.agent_id)
                if agent is None:
                    continue
                agent.state.balances[token_id] = agent.state.balances.get(token_id, 0) + share
                agent_allocations = per_agent.setdefault(pos.agent_id, {})
                agent_allocations[token_id] = agent_allocations.get(token_id, 0) + share

        self._bus.emit(Event(
            type=EventType.LP_FEES_DISTRIBUTED,
            round=round_num, timestamp=ts,
            data={"total_fees": total_fees, "per_agent": per_agent},
        ))

    def _build_context(
        self,
        agent: Agent,
        round_num: int,
        ts: int,
        epoch: int,
        pending_actions: list[Action] | None = None,
    ) -> DecisionContext:
        """Build decision context for an agent."""
        execution_pending = self._execution_model.pending_actions_for_agent(
            agent,
            pending_actions or [],
            round_num,
            self._build_ordering_context(),
        )
        # PRD US-001 line 108: surface current_slot/current_leader on
        # DecisionContext so agents (notably JitoSearcher) can read them
        # without having to reach into OrderingContext via extras.
        current_slot = (
            round_num if self._execution_model.supports_slot_execution() else None
        )
        current_leader = (
            self._execution_model.current_leader(current_slot)
            if current_slot is not None
            else None
        )
        # PRD US-004 line 368: per-agent drop reasons collected during the
        # previous slot, keyed by canonical drop reason. None when nothing
        # was dropped for this agent last slot.
        last_drop_reasons = self._last_drop_reasons_by_agent.get(agent.agent_id)
        # PRD US-013 line 1024: bundle submission side-channel for
        # ``JitoSearcher.decide``. Only attached when the execution model
        # supports submit_bundle (Solana-like).
        submit_bundle_fn = getattr(self._execution_model, "submit_bundle", None)
        visible_agents = None
        if self._config.visible_agents_provider is not None:
            visible_agents = self._config.visible_agents_provider(agent, self._agents, round_num)
        if self._is_world:
            all_states = self._info_filter.filter_all_market_states(agent, self._market.get_all_states())
            feed_prices = None
            if self._round_feed_prices is not None:
                feed_prices = self._info_filter.filter_feed_prices(agent, dict(self._round_feed_prices))
            belief = None
            if self._config.belief_provider is not None:
                belief = self._config.belief_provider(agent, None, all_states, round_num)
            extra: dict[str, Any] = {"rng": self._agent_rngs[agent.agent_id]}
            first_market = next(iter(self._market.markets.values()), None)
            if first_market is not None:
                extra["weight_scale"] = self._infer_weight_scale(first_market)
                extra["price_scale"] = self._infer_price_scale(first_market)
            token_decimals: dict[TokenId, int] = {}
            for sub_market in self._market.markets.values():
                for tok in getattr(sub_market, "_tokens", None) or ():
                    tok_id = getattr(tok, "id", None)
                    tok_dec = getattr(tok, "decimals", None)
                    if tok_id is not None and tok_dec is not None:
                        token_decimals.setdefault(tok_id, int(tok_dec))
            if token_decimals:
                extra["token_decimals"] = token_decimals
            ctx = WorldContext(
                market_state=None,
                current_round=round_num,
                total_rounds=self._config.num_rounds,
                timestamp=ts,
                epoch=epoch,
                agent_state=agent.state,
                belief=belief,
                feed_prices=feed_prices,
                visible_agents=visible_agents,
                pending_actions=execution_pending,
                parameters=self._parameters,
                priority_fee_market=self.priority_fee_market,
                current_slot=current_slot,
                current_leader=current_leader,
                last_drop_reasons=last_drop_reasons,
                submit_bundle=submit_bundle_fn,
                resolve_locks=self._resolve_action_locks,
                extra=extra,
                all_markets=all_states,
            )
        else:
            state = self._market.get_state()
            filtered_state = self._info_filter.filter_market_state(agent, state)

            feed_prices = None
            if self._round_feed_prices is not None:
                feed_prices = self._info_filter.filter_feed_prices(agent, dict(self._round_feed_prices))
            belief = None
            if self._config.belief_provider is not None:
                belief = self._config.belief_provider(agent, filtered_state, None, round_num)
            elif feed_prices is not None:
                if self._config.numeric_mode.use_float:
                    belief = {
                        token_id: float(price)
                        for token_id, price in feed_prices.items()
                        if token_id in filtered_state.tokens
                    }
                else:
                    belief = {
                        token_id: int(price)
                        for token_id, price in feed_prices.items()
                        if token_id in filtered_state.tokens
                    }

            extra: dict[str, Any] = {"rng": self._agent_rngs[agent.agent_id]}
            extra["weight_scale"] = self._infer_weight_scale(self._market)
            extra["price_scale"] = self._infer_price_scale(self._market)
            market_tokens = getattr(self._market, "_tokens", None)
            if market_tokens:
                token_decimals: dict[TokenId, int] = {}
                for tok in market_tokens:
                    tok_id = getattr(tok, "id", None)
                    tok_dec = getattr(tok, "decimals", None)
                    if tok_id is not None and tok_dec is not None:
                        token_decimals[tok_id] = int(tok_dec)
                if token_decimals:
                    extra["token_decimals"] = token_decimals
            if isinstance(self._market, LiquidityPool):
                lp_position = self._market.get_lp_position(agent.agent_id)
                lp_state = self._market.get_lp_state()
                extra["lp_position"] = lp_position
                extra["lp_state"] = lp_state
                total_deposited = lp_state.total_deposited
                if total_deposited:
                    extra["fee_yield"] = float(lp_state.accumulated_fees) / float(total_deposited)
                else:
                    extra["fee_yield"] = 0.0
                extra["unrealized_loss"] = 0.0
                extra["in_range"] = None
                if isinstance(lp_position, ConcentratedLPPosition):
                    extra["in_range"] = bool(lp_position.in_range)
                    record = getattr(self._market, "position_record", None)
                    if callable(record):
                        rec = record(agent.agent_id, lp_position.position_id)
                        if rec is not None and rec.liquidity > 0:
                            try:
                                value_now = self._market.position_value_in_b(rec)
                                value_hodl = self._market.hodl_value_in_b(rec)
                                if value_hodl > 0:
                                    il = max(
                                        0.0,
                                        1.0 - float(value_now) / float(value_hodl),
                                    )
                                    extra["unrealized_loss"] = il
                            except Exception:
                                pass
                extra["supports_lp_rebalance"] = bool(
                    getattr(self._market, "supports_lp_rebalance", False)
                )

            ctx = DecisionContext(
                market_state=filtered_state,
                current_round=round_num,
                total_rounds=self._config.num_rounds,
                timestamp=ts,
                epoch=epoch,
                agent_state=agent.state,
                belief=belief,
                feed_prices=feed_prices,
                visible_agents=visible_agents,
                pending_actions=execution_pending,
                parameters=self._parameters,
                priority_fee_market=self.priority_fee_market,
                current_slot=current_slot,
                current_leader=current_leader,
                last_drop_reasons=last_drop_reasons,
                submit_bundle=submit_bundle_fn,
                resolve_locks=self._resolve_action_locks,
                extra=extra,
            )

        return ctx

    def _build_ordering_context(self, market_name: str | None = None) -> OrderingContext:
        agent_states = {agent.agent_id: agent.state for agent in self._agents}
        slot = self._execution_model.current_slot()
        leader = self._execution_model.current_leader(slot) if slot is not None else None
        if self._is_world:
            all_states = self._market.get_all_states()
            return OrderingContext(
                market_state=all_states.get(market_name) if market_name is not None else None,
                all_market_states=all_states,
                agent_states=agent_states,
                current_slot=slot,
                current_leader=leader,
            )
        return OrderingContext(
            market_state=self._market.get_state(),
            agent_states=agent_states,
            current_slot=slot,
            current_leader=leader,
        )

    def _market_name_for(self, market: Market) -> str | None:
        if not self._is_world:
            return None
        for name, current_market in self._market.markets.items():
            if current_market is market:
                return name
        return None

    def _collect_round_prices(self) -> dict[TokenId, Numeric] | None:
        if not self._is_world:
            market = self._market
            if isinstance(market, PricedMarket):
                return dict(market.get_prices())
            return None

        prices: dict[TokenId, Numeric] = {}
        saw_priced_market = False
        for market_name, market in self._market.markets.items():
            if not isinstance(market, PricedMarket):
                continue
            saw_priced_market = True
            for token_id, price in market.get_prices().items():
                prices[f"{market_name}:{token_id}"] = price
        if saw_priced_market:
            return prices
        return None

    def _register_agent_event_handlers(self) -> None:
        for agent in self._agents:
            if type(agent).on_event is Agent.on_event:
                continue
            self._bus.on_any(agent.on_event)

    def _build_snapshot(self, round_num: int, ts: int, epoch: int) -> RoundSnapshot:
        agent_states = {a.agent_id: copy.deepcopy(a.state) for a in self._agents}

        current_slot: int | None = None
        current_leader: str | None = None
        if isinstance(self._clock, SolanaSlotClock):
            current_slot = self._clock.current_slot
            if current_slot is not None:
                current_leader = self._execution_model.current_leader(current_slot)

        bundle_outcomes = self._collect_bundle_outcomes(current_slot, round_num)
        metrics = self._collect_snapshot_metrics()

        if self._is_world:
            return RoundSnapshot(
                round=round_num,
                timestamp=ts,
                epoch=epoch,
                agent_states=agent_states,
                all_market_states=self._market.get_all_states(),
                current_slot=current_slot,
                current_leader=current_leader,
                bundle_outcomes=bundle_outcomes,
                metrics=metrics,
            )
        return RoundSnapshot(
            round=round_num,
            timestamp=ts,
            epoch=epoch,
            agent_states=agent_states,
            market_state=self._market.get_state(),
            current_slot=current_slot,
            current_leader=current_leader,
            bundle_outcomes=bundle_outcomes,
            metrics=metrics,
        )

    def _collect_snapshot_metrics(self) -> dict[str, Any]:
        """Surface engine-level metrics for ``RoundSnapshot.metrics`` (PRD US-012 line 973).

        Emits:
        * ``validator_revenue`` -> deep copy of the per-(epoch, pubkey)
          ``ValidatorEpochRevenue`` accumulator (PRD US-012).
        * ``jito_searcher`` -> per-agent snapshot dict for each ``JitoSearcher``
          carrying a ``synthetic: True`` marker (PRD US-013 line 1053).

        Returns ``{}`` when nothing has accrued so non-Solana snapshots stay
        empty.
        """
        from defi_sim.agents.jito_searcher import JitoSearcher

        metrics: dict[str, Any] = {}
        if self._validator_revenue_by_epoch:
            metrics["validator_revenue"] = copy.deepcopy(
                self._validator_revenue_by_epoch
            )
        searcher_metrics: dict[str, Any] = {}
        bound_auction: Any = None
        execution = self._execution_model
        if isinstance(execution, SolanaLikeExecution):
            bound_auction = execution.bundle_auction
        for agent in self._agents:
            if isinstance(agent, JitoSearcher):
                searcher_metrics[agent.agent_id] = agent.metrics.to_snapshot_dict(
                    bundle_auction=bound_auction
                )
        if searcher_metrics:
            metrics["jito_searcher"] = searcher_metrics

        # Whirlpool per-round CLMM telemetry: tick crossings, LP fees split
        # by token side, active liquidity. Drained off the market each round
        # so the snapshot carries a per-round delta rather than a cumulative.
        from defi_sim.markets.whirlpool import WhirlpoolMarket

        whirlpool_payload: dict[str, Any] = {}
        if self._is_world:
            for name, mkt in self._market.markets.items():
                if isinstance(mkt, WhirlpoolMarket):
                    mkt.tick_lp_round_stats()
                    whirlpool_payload[name] = mkt.pop_round_telemetry()
        elif isinstance(self._market, WhirlpoolMarket):
            self._market.tick_lp_round_stats()
            whirlpool_payload["__default__"] = self._market.pop_round_telemetry()
        if whirlpool_payload:
            metrics["whirlpool"] = whirlpool_payload
        return metrics

    def _collect_bundle_outcomes(
        self, current_slot: int | None, round_num: int
    ) -> list[BundleOutcome]:
        """Surface per-slot bundle outcomes for ``RoundSnapshot`` (PRD US-011 line 891).

        Walks the execution model's per-slot telemetry (``_last_slot_selected_bundles``
        / ``_last_slot_dropped_bundles`` populated by the bundle pre-stage) and
        materializes a ``BundleOutcome`` per bundle with land/revert/drop status,
        revenue split, and ALT usage. Returns ``[]`` when the execution model is
        not ``SolanaLikeExecution`` or no bundle auction is configured.
        """
        execution = self._execution_model
        if not isinstance(execution, SolanaLikeExecution):
            return []
        auction = execution.bundle_auction
        if auction is None:
            return []

        slot = current_slot if current_slot is not None else round_num
        share = auction.jito_stake_pool_share
        outcomes: list[BundleOutcome] = []

        for index, (bundle, result) in enumerate(execution._last_slot_selected_bundles):
            paid_lamports = sum(tp.lamports for tp in result.paid_tips)
            stake_pool = int(round(paid_lamports * share))
            validator = paid_lamports - stake_pool
            outcomes.append(
                BundleOutcome(
                    slot=slot,
                    bundle_index=index,
                    status="reverted" if result.reverted else "landed",
                    tip_lamports=bundle.tip_lamports,
                    validator_revenue_lamports=validator,
                    stake_pool_revenue_lamports=stake_pool,
                    alt_ids=tuple(
                        sorted({alt for tx in bundle.txs for alt in tx.lookup_tables})
                    ),
                    num_txs=len(bundle.txs),
                    total_cu=bundle.total_cu,
                    failed_at_index=result.failed_at_index,
                )
            )

        selected_count = len(outcomes)
        for offset, (bundle, reason) in enumerate(execution._last_slot_dropped_bundles):
            outcomes.append(
                BundleOutcome(
                    slot=slot,
                    bundle_index=selected_count + offset,
                    status="dropped",
                    tip_lamports=bundle.tip_lamports,
                    validator_revenue_lamports=0,
                    stake_pool_revenue_lamports=0,
                    alt_ids=tuple(
                        sorted({alt for tx in bundle.txs for alt in tx.lookup_tables})
                    ),
                    num_txs=len(bundle.txs),
                    total_cu=bundle.total_cu,
                    drop_reason=reason,
                )
            )
        return outcomes

    def _get_market_state(self) -> MarketSnapshot | None:
        if self._is_world:
            return None
        return self._market.get_state()

    def _find_agent(self, agent_id: AgentId) -> Agent | None:
        for a in self._agents:
            if a.agent_id == agent_id:
                return a
        return None

    def _record_oracle_pulls_and_emit_stale(
        self,
        executed: list[Any],
        slot: int,
        ts: int | float,
    ) -> None:
        """PRD US-006 line 494-496: track ``OracleUpdateAction`` pulls and
        auto-emit ``OracleStaleEvent`` when a registered pull-oracle's
        staleness exceeds its tolerance.

        Walks ``SlotOutcome.executed`` looking for ``OracleUpdateAction``
        entries (the canonical signal that a pull happened this slot) and
        records the slot in ``_oracle_pull_slots``. After tallying, walks
        the registered pull oracles and emits one ``OracleStaleEvent`` per
        oracle that crossed into stale territory this slot. The
        ``_oracle_stale_emitted`` map deduplicates so a single contiguous
        stale window produces a single event rather than one per slot.
        """
        from defi_sim.engine.oracles.metrics import make_oracle_stale_event
        from defi_sim.engine.oracles.source import OracleUpdateAction

        for executed_action in executed:
            inner = getattr(executed_action, "action", executed_action)
            if isinstance(inner, OracleUpdateAction) and inner.oracle_id:
                self._oracle_pull_slots.setdefault(inner.oracle_id, []).append(slot)

        for oracle_id, oracle in self._pull_oracles.items():
            is_stale = getattr(oracle, "is_stale", None)
            last_pull_slot = getattr(oracle, "last_pull_slot", None)
            if is_stale is None or last_pull_slot is None:
                continue
            if not is_stale(slot):
                self._oracle_stale_emitted.pop(oracle_id, None)
                continue
            last_pulled = last_pull_slot()
            already_emitted_for = self._oracle_stale_emitted.get(oracle_id)
            sentinel = last_pulled if last_pulled is not None else -1
            if already_emitted_for == sentinel:
                continue
            self._oracle_stale_emitted[oracle_id] = sentinel
            event = make_oracle_stale_event(
                round=slot,
                timestamp=ts,
                slot=slot,
                oracle_id=oracle_id,
                last_update_slot=last_pulled,
            )
            self._bus.emit(event)

    def _emit_bundle_tip_outcomes(self, slot: int, ts: int | float) -> None:
        """Emit ``BundleTipPaid`` events and append per-landed-bundle entries
        to the tip-outcomes ledger (PRD US-011 line 839).

        Walks the execution model's slot telemetry — the same source the
        per-snapshot ``BundleOutcome`` collection reads from — and skips
        any bundle the auction or the fork roll has already marked
        reverted. The ledger entry mirrors the event payload so consumers
        replaying it (US-012 validator economics) do not need access to
        the live execution model.
        """
        execution = self._execution_model
        if not isinstance(execution, SolanaLikeExecution):
            return
        if not execution._last_slot_selected_bundles:
            return
        leader = execution.current_leader(slot)
        auction = execution.bundle_auction
        share = auction.jito_stake_pool_share if auction is not None else 0.0
        from defi_sim.agents.jito_searcher import JitoSearcher
        for index, (bundle, result) in enumerate(execution._last_slot_selected_bundles):
            if result.reverted:
                continue
            paid_lamports = sum(tp.lamports for tp in result.paid_tips)
            if paid_lamports <= 0:
                continue
            payload = BundleTipPaidEvent(
                slot=slot,
                bundle_index=index,
                leader_pubkey=leader,
                tip_lamports=paid_lamports,
                tip_payments=tuple(result.paid_tips),
                jito_stake_pool_share=share,
                searcher_id=bundle.searcher_id,
                strategy=bundle.strategy,
            )
            self._tip_outcomes.append(payload)
            self._bus.emit(Event(
                type=EventType.BUNDLE_TIP_PAID,
                round=slot,
                timestamp=ts,
                data={"bundle_tip_paid": payload},
            ))
            # PRD US-013 line 1049/1075: credit landing back to the
            # searcher's metrics so landing rate / tip ROI / tips-paid
            # surface in ``metrics.jito_searcher.<agent_id>``. Submitted-
            # but-dropped bundles already counted toward the denominator
            # via ``record_submitted`` in ``decide()``; this only fires
            # for bundles that actually landed and paid a tip.
            if bundle.searcher_id is None or bundle.strategy is None:
                continue
            searcher = self._find_agent(bundle.searcher_id)
            if isinstance(searcher, JitoSearcher):
                searcher.metrics.record_landed(
                    bundle.strategy,
                    tip_lamports=paid_lamports,
                    realized_ev_lamports=int(bundle.expected_ev_lamports),
                )

    def _credit_validator_revenue(self, slot: int) -> None:
        """Credit per-leader-slot bundle tip revenue (PRD US-012 line 964-967).

        For each landed bundle in the just-finished slot:
        - Look up the Validator agent whose pubkey matches the slot's leader.
        - If client == ``jito_solana``: credit ``validator.balances['SOL']`` by
          ``tip_lamports * (1 - stake_pool_share)`` and credit the configured
          ``stake_pool_address`` agent (if any) by ``tip_lamports * stake_pool_share``.
        - If client == ``vanilla``: no MEV credit (regular block rewards only).
        Reverted bundles do not pay tips and are skipped.
        """
        execution = self._execution_model
        if not isinstance(execution, SolanaLikeExecution):
            return
        if not execution._last_slot_selected_bundles:
            return
        leader_pubkey = execution.current_leader(slot)
        if leader_pubkey is None:
            return
        from defi_sim.agents.validator import Validator
        validator: Validator | None = None
        for agent in self._agents:
            if isinstance(agent, Validator) and agent.params.pubkey == leader_pubkey:
                validator = agent
                break
        if validator is None:
            return
        if validator.params.client != "jito_solana":
            return
        share = validator.params.stake_pool_share
        pool_id = validator.params.stake_pool_address
        pool_agent = self._find_agent(pool_id) if pool_id is not None else None
        epoch = self._validator_epoch_for_slot(slot)
        for _bundle, result in execution._last_slot_selected_bundles:
            if result.reverted:
                continue
            paid_lamports = sum(tp.lamports for tp in result.paid_tips)
            if paid_lamports <= 0:
                continue
            stake_pool_amount = int(round(paid_lamports * share))
            validator_amount = paid_lamports - stake_pool_amount
            validator.state.balances["SOL"] = (
                validator.state.balances.get("SOL", 0) + validator_amount
            )
            if pool_agent is not None and stake_pool_amount > 0:
                pool_agent.state.balances["SOL"] = (
                    pool_agent.state.balances.get("SOL", 0) + stake_pool_amount
                )
            epoch_bucket = self._validator_revenue_by_epoch.setdefault(epoch, {})
            entry = epoch_bucket.get(validator.params.pubkey)
            if entry is None:
                entry = ValidatorEpochRevenue(
                    epoch=epoch,
                    pubkey=validator.params.pubkey,
                    client=validator.params.client,
                )
                epoch_bucket[validator.params.pubkey] = entry
            entry.validator_revenue_lamports += validator_amount
            entry.stake_pool_revenue_lamports += stake_pool_amount

    def _validator_epoch_for_slot(self, slot: int) -> int:
        """Resolve the epoch index for ``slot`` (PRD US-012 line 969).

        Prefers the active ``SolanaSlotClock``'s ``epoch_length_slots``;
        falls back to the leader schedule's epoch length when the clock is
        not Solana-native; defaults to a single epoch (0) otherwise.
        """
        if isinstance(self._clock, SolanaSlotClock):
            return slot // self._clock.epoch_length_slots
        execution = self._execution_model
        if isinstance(execution, BatchExecution):
            sched = getattr(execution, "_leader_schedule", None)
            if sched is not None:
                return slot // sched._epoch_length_slots
        return 0

    def _resolve_action_locks(self, action: Action) -> LockedAction | None:
        """Engine-side ``LockResolver`` lookup for ``execute_slot``.

        PRD US-003 step 4 / line 270: lock resolution must run before the
        scheduler. Routes each action to its owning market and delegates to
        the market's ``resolve_locks``. Returning ``None`` when the routed
        market does not implement ``LockResolver`` triggers the strict
        ``MISSING_LOCK_RESOLVER`` admission rejection in
        ``execute_slot`` — the action never silently degrades to
        serial-with-empty-locks.
        """
        if isinstance(action, MultiMarketAction):
            mkt = self._market.markets.get(action.market_name) if self._is_world else None
            inner = action.inner
            if isinstance(mkt, LockResolver):
                inner_locked = mkt.resolve_locks(inner, mkt)
                return LockedAction(
                    action=action,
                    read_locks=inner_locked.read_locks,
                    write_locks=inner_locked.write_locks,
                )
            return None
        if not self._is_world and isinstance(self._market, LockResolver):
            return self._market.resolve_locks(action, self._market)
        return None

    def _action_executor_for_slot(
        self,
        round_num: int,
        ts: int,
        market_override: Market | None = None,
    ):
        """Return an ActionExecutor closure usable from inside execute_slot().

        World-mode behaviour: if ``market_override`` is None the closure
        unwraps a ``MultiMarketAction`` and dispatches to the correct market
        from ``self._market.markets``. Callers that already know the target
        market (legacy per-market routing path) can pass ``market_override``
        to skip the lookup.
        """

        def execute(action: Action, slot: int) -> ExecutedAction:
            planned = self._reserve_execution_cost(action, round_num, ts)
            if planned is None:
                return ExecutedAction(
                    action=action,
                    execution_cost=0,
                    cost_token=None,
                    succeeded=False,
                    failure_reason="execution cost reservation failed",
                )

            prior_capture = self._slot_action_capture
            capture: dict[str, Any] = {"success": None, "reason": None}
            self._slot_action_capture = capture
            try:
                self._execute_action(
                    action,
                    round_num,
                    ts,
                    market_override=market_override,
                    execution_cost=planned.execution_cost,
                    cost_token=planned.cost_token,
                )
            finally:
                self._slot_action_capture = prior_capture

            # Default semantics: if nothing emitted (e.g. predicate-skipped
            # ConditionalAction), treat as succeeded with no failure reason.
            failed = capture["success"] is False
            return ExecutedAction(
                action=action,
                execution_cost=planned.execution_cost,
                cost_token=planned.cost_token,
                succeeded=not failed,
                failure_reason=capture["reason"] if failed else None,
            )

        return execute

    def _plan_actions(
        self,
        actions: list[Action],
        round_num: int,
        ts: int,
    ) -> list[_PlannedAction]:
        planned: list[_PlannedAction] = []
        for action in actions:
            reserved = self._reserve_execution_cost(action, round_num, ts)
            if reserved is not None:
                planned.append(reserved)
        return planned

    def _reserve_execution_cost(
        self,
        action: Action,
        round_num: int,
        ts: int,
    ) -> _PlannedAction | None:
        agent = self._find_agent(action.agent_id)
        if agent is None:
            return _PlannedAction(action=action, execution_cost=0, cost_token=None)

        execution_cost = self._execution_model.cost(action, round_num)
        cost_token = self._execution_model.cost_token(action)
        if execution_cost <= 0:
            return _PlannedAction(action=action, execution_cost=execution_cost, cost_token=cost_token)
        if cost_token is None:
            self._emit_action_dropped(action, round_num, ts, "execution cost token is unavailable")
            return None

        balance = agent.state.balance(cost_token)
        if balance < execution_cost:
            self._emit_action_dropped(action, round_num, ts, f"insufficient {cost_token} for execution cost")
            return None

        agent.state.balances[cost_token] = balance - execution_cost
        return _PlannedAction(action=action, execution_cost=execution_cost, cost_token=cost_token)

    def _refund_execution_cost(self, agent: Agent, execution_cost: Numeric, cost_token: TokenId | None) -> None:
        if execution_cost > 0 and cost_token is not None:
            agent.state.balances[cost_token] = agent.state.balances.get(cost_token, 0) + execution_cost

    def _apply_balance_deltas(self, agent: Agent, deltas: dict[TokenId, Numeric]) -> None:
        for token_id, delta in deltas.items():
            agent.state.balances[token_id] = agent.state.balances.get(token_id, 0) + delta

    def _apply_execution_result(
        self,
        primary_agent: Agent,
        result: ExecutionResult,
        market: Market | None = None,
    ) -> None:
        self._apply_balance_deltas(primary_agent, result.token_deltas)
        volume = result.volume
        if volume is None:
            volume = sum(abs(delta) for delta in result.token_deltas.values())
        primary_agent.state.cumulative_volume += volume
        primary_agent.state.realized_pnl += self._mark_pnl(market, result.token_deltas)

        for other_agent_id, deltas in result.other_agent_deltas.items():
            other_agent = self._find_agent(other_agent_id)
            if other_agent is None:
                continue
            self._apply_balance_deltas(other_agent, deltas)
            if other_agent_id in result.other_agent_volumes:
                other_agent.state.cumulative_volume += result.other_agent_volumes[other_agent_id]
            other_agent.state.realized_pnl += self._mark_pnl(market, deltas)

    @staticmethod
    def _mark_pnl(
        market: Market | None,
        deltas: dict[TokenId, Numeric],
    ) -> Numeric:
        """Mark ``deltas`` to a quote token using the executing market.

        Falls back to 0 when the market doesn't expose a ``quote_pnl``
        method — keeps non-Whirlpool flows byte-identical until they
        opt in. PnL is summed into ``agent.state.realized_pnl`` in raw
        quote-token units (USDC base units for SOL/USDC), so the
        existing fixed-mode ``int()`` coercion at end-of-run preserves
        sub-dollar fee drag instead of rounding it away.
        """
        if market is None or not deltas:
            return 0
        quote_pnl = getattr(market, "quote_pnl", None)
        if not callable(quote_pnl):
            return 0
        try:
            return quote_pnl(deltas)
        except Exception:
            return 0

    def _snapshot_agent_states(self) -> dict[AgentId, AgentState]:
        return {agent.agent_id: copy.deepcopy(agent.state) for agent in self._agents}

    def _restore_agent_states(self, backups: dict[AgentId, AgentState]) -> None:
        for agent in self._agents:
            if agent.agent_id in backups:
                agent.state = copy.deepcopy(backups[agent.agent_id])

    def _snapshot_jito_searcher_metrics(self) -> dict[AgentId, Any]:
        from defi_sim.agents.jito_searcher import JitoSearcher
        return {
            agent.agent_id: copy.deepcopy(agent.metrics)
            for agent in self._agents
            if isinstance(agent, JitoSearcher)
        }

    def _restore_jito_searcher_metrics(self, backups: dict[AgentId, Any]) -> None:
        if not backups:
            return
        from defi_sim.agents.jito_searcher import JitoSearcher
        for agent in self._agents:
            if isinstance(agent, JitoSearcher) and agent.agent_id in backups:
                agent.metrics = copy.deepcopy(backups[agent.agent_id])

    # US-005 audit (PRD line 406): bundle-mutable state surface.
    #
    # Audit of every Phase 1 mechanism state a bundle of N sub-actions can
    # mutate, and which primitive currently covers it:
    #
    #   - markets ........................ market.copy() (per-market) /
    #                                      world: per-child copy via
    #                                      _snapshot_world_markets()
    #   - agent balances ................. _snapshot_agent_states()
    #   - agent cumulative_volume / pnl .. _snapshot_agent_states()
    #   - fee accruals (_round_fee_splits,
    #     _fee_destination_balances,
    #     _pending_lp_fees) .............. NOT dirtied per sub-action today
    #                                      (atomic paths buffer locally and
    #                                      route once at end of success),
    #                                      but BundleAuction (1.7) will
    #                                      route per-sub-action so we
    #                                      capture them here.
    #   - oracle values (_last_feed_prices,
    #     _round_feed_prices,
    #     _price_history) ................ Set once per round outside the
    #                                      action loop today; captured
    #                                      defensively for bundles that
    #                                      may consume oracle pulls
    #                                      (Pyth Pull / Switchboard
    #                                      On-Demand) per-sub-action.
    #   - priority fee market ............ Not yet implemented (1.6); the
    #                                      capture/restore plumbing here
    #                                      will be extended once the
    #                                      mechanism lands.
    #   - validator tip revenue .......... Not yet implemented (1.7);
    #                                      same extension path.
    #   - bundle outcomes ................ Not yet implemented (1.7);
    #                                      same extension path.
    #   - RNG state ...................... Engine RNGs (agent / ordering /
    #                                      feed / engine / submission)
    #                                      captured for replay-safety
    #                                      across atomic boundaries.
    #
    # Helpers below back the engine.atomic_state_boundary() context
    # manager (PRD line 410) and the BundleAuction revert path (PRD 1.7
    # step 4). Avoid introducing a second snapshot format — these reuse
    # copy.deepcopy() and the existing per-market .copy() primitive.
    #
    # OPTIMIZE-2.4: deep copy is the v1 choice (PRD US-005 line 411).
    # Budget claim: a bundle of 5 txs taking 5 snapshots fits the typical
    # hot-path budget for Phase-1-sized markets — see the perf smoke test
    # at tests/engine/test_state_rollback_perf.py. If 2.4 calibration runs
    # surface this as a bottleneck (e.g., wide world specs or large agent
    # rosters in CI), swap to copy-on-write via versioned dicts. Grep this
    # tag from the perf or profile job to find the call site.
    def _snapshot_bundle_mutable_state(self) -> dict[str, Any]:
        if self._is_world:
            market_backup: Any = {
                name: child.copy() for name, child in self._market.markets.items()
            }
        else:
            market_backup = self._market.copy() if hasattr(self._market, "copy") else None
        # PRD US-005 line 409 / US-014 line 1123: priority fee market observations
        # and EWMA baselines are mutable per-slot state; atomic rollback and fork
        # reorgs must restore them so reverted slots' observations don't bleed
        # into post-rollback quotes.
        pfm = self.priority_fee_market
        if pfm is not None:
            pfm_snapshot: Any = {
                "observations": {
                    account: list(buf)
                    for account, buf in pfm._observations.items()
                },
                "ewma_baseline": dict(pfm._ewma_baseline),
            }
        else:
            pfm_snapshot = None
        return {
            "agents": self._snapshot_agent_states(),
            "market": market_backup,
            "fee_destination_balances": copy.deepcopy(self._fee_destination_balances),
            "pending_lp_fees": copy.deepcopy(self._pending_lp_fees),
            "round_fee_splits": copy.deepcopy(self._round_fee_splits),
            "last_feed_prices": copy.deepcopy(self._last_feed_prices),
            "round_feed_prices": copy.deepcopy(self._round_feed_prices),
            "priority_fee_market": pfm_snapshot,
            # PRD US-014 line 1124 (past-slot tip-revert under fork): include
            # the per-(epoch, validator-pubkey) revenue accumulator so a fork
            # hit at slot N with depth d that restores from start-of-slot
            # ``N - d`` also rolls back any credits accrued during the
            # abandoned range ``[N - d, N - 1]``. Validator agent balances
            # revert via ``agents`` above; this dict is the engine-level
            # mirror that ``_credit_validator_revenue`` writes into.
            "validator_revenue_by_epoch": copy.deepcopy(self._validator_revenue_by_epoch),
            # PRD US-011 line 839 / US-005 line 410: durable bundle tip
            # ledger snapshots alongside the rest of the engine's mutable
            # state so atomic rollbacks and fork reverts drop the entries
            # the abandoned slot emitted.
            "tip_outcomes": list(self._tip_outcomes),
            # PRD US-013 line 1049 (fork rollback): JitoSearcher tracking
            # metrics live on ``agent.metrics``, not ``agent.state``, so
            # the ``agents`` snapshot above doesn't cover them. Capture
            # them separately so a fork at slot N with depth d also
            # rolls back landing/tip-paid counters from the abandoned
            # range ``[N - d, N - 1]``.
            "jito_searcher_metrics": self._snapshot_jito_searcher_metrics(),
            "rng_states": {
                "agent": self._agent_rng.bit_generator.state,
                "ordering": self._ordering_rng.bit_generator.state,
                "feed": self._feed_rng.bit_generator.state,
                "engine": self._engine_rng.bit_generator.state,
                "submission": self._submission_rng.bit_generator.state,
            },
        }

    def _restore_bundle_mutable_state(self, snapshot: dict[str, Any]) -> None:
        self._restore_agent_states(snapshot["agents"])
        market_backup = snapshot["market"]
        if self._is_world and isinstance(market_backup, dict):
            for name, restored in market_backup.items():
                if name in self._market.markets:
                    self._market.markets[name] = restored
        elif market_backup is not None:
            self._restore_market_reference(self._market, market_backup)
        self._fee_destination_balances = copy.deepcopy(snapshot["fee_destination_balances"])
        self._pending_lp_fees = copy.deepcopy(snapshot["pending_lp_fees"])
        self._round_fee_splits = copy.deepcopy(snapshot["round_fee_splits"])
        self._last_feed_prices = copy.deepcopy(snapshot["last_feed_prices"])
        self._round_feed_prices = copy.deepcopy(snapshot["round_feed_prices"])
        self._validator_revenue_by_epoch = copy.deepcopy(
            snapshot.get("validator_revenue_by_epoch", {})
        )
        self._tip_outcomes = list(snapshot.get("tip_outcomes", []))
        self._restore_jito_searcher_metrics(snapshot.get("jito_searcher_metrics", {}))
        pfm_snapshot = snapshot.get("priority_fee_market")
        pfm = self.priority_fee_market
        if pfm is not None and pfm_snapshot is not None:
            from collections import deque

            pfm._observations = {
                account: deque(entries, maxlen=pfm._window_slots)
                for account, entries in pfm_snapshot["observations"].items()
            }
            pfm._ewma_baseline = dict(pfm_snapshot["ewma_baseline"])
        rng_states = snapshot["rng_states"]
        self._agent_rng.bit_generator.state = rng_states["agent"]
        self._ordering_rng.bit_generator.state = rng_states["ordering"]
        self._feed_rng.bit_generator.state = rng_states["feed"]
        self._engine_rng.bit_generator.state = rng_states["engine"]
        self._submission_rng.bit_generator.state = rng_states["submission"]

    @contextmanager
    def atomic_state_boundary(self) -> Iterator[AtomicBoundary]:
        """Bundle-local rollback boundary for ``BundleAuction`` (PRD US-005).

        Snapshots every bundle-mutable state surface (agents, market(s),
        fee accruals, oracle/feed prices, engine RNGs) on entry via the
        existing ``_snapshot_bundle_mutable_state`` primitive. On exit:
          * if an exception propagates, the snapshot is restored and the
            exception re-raises;
          * if the yielded ``AtomicBoundary.rollback()`` was called, the
            snapshot is restored;
          * otherwise the boundary commits and the snapshot is dropped.
        """
        snapshot = self._snapshot_bundle_mutable_state()
        boundary = AtomicBoundary()
        try:
            yield boundary
        except BaseException:
            self._restore_bundle_mutable_state(snapshot)
            raise
        if boundary.should_rollback:
            self._restore_bundle_mutable_state(snapshot)

    def _execute_bundle_atomically(
        self,
        actions: list[Action],
        round_num: int,
        ts: int,
        *,
        market_override: Market | None = None,
    ) -> dict[str, Any]:
        """Bundle revert path implemented in terms of ``atomic_state_boundary``
        (PRD US-005 line 424; US-011 step 4).

        Walks ``actions`` in declared order via the existing per-action
        dispatch (``_execute_action``). If any sub-action emits
        ``ACTION_FAILED``, the entire bundle is reverted via the existing
        rollback primitive so state mutations from positions ``0..j`` are
        undone. Bundle-level event emission (``BundleTipPaid``, tip-position
        semantics, revenue routing) is left to the future ``BundleAuction``
        wrapper (PRD US-011) — this helper is the rollback core only.

        Returns a dict with:
          * ``reverted``: ``True`` if any sub-action failed
          * ``failed_at_index``: index of first-failing action, or ``None``
          * ``failed_reason``: error string from the failing action, or ``None``
          * ``executed``: list of actions applied (only populated on success)
        """
        failure: dict[str, Any] = {}
        executed_so_far: list[Action] = []

        def _on_failed(event: Event) -> None:
            if "index" in failure:
                return
            failure["index"] = len(executed_so_far)
            result = event.data.get("result")
            failure["reason"] = (
                event.data.get("reason")
                or (result.error if result is not None else None)
                or "execution failed"
            )

        self._bus.on(EventType.ACTION_FAILED, _on_failed)
        try:
            with self.atomic_state_boundary() as boundary:
                for action in actions:
                    self._execute_action(
                        action,
                        round_num,
                        ts,
                        market_override=market_override,
                    )
                    if "index" in failure:
                        boundary.rollback()
                        break
                    executed_so_far.append(action)
        finally:
            self._bus.off(EventType.ACTION_FAILED, _on_failed)

        if "index" in failure:
            return {
                "reverted": True,
                "failed_at_index": failure["index"],
                "failed_reason": failure["reason"],
                "executed": [],
            }
        return {
            "reverted": False,
            "failed_at_index": None,
            "failed_reason": None,
            "executed": list(executed_so_far),
        }

    def _execute_bundle_for_ctx(
        self,
        round_num: int,
        ts: int,
    ) -> Callable[[Bundle, int], BundleExecutionResult]:
        """Build the ``SlotContext.execute_bundle`` callback for this round.

        PRD US-011 line 840: the bundle pre-stage in ``SolanaLikeExecution``
        delegates atomic execution to this engine-supplied callback. The
        wrapper flattens the bundle's inner txs into actions, runs them under
        the rollback boundary defined by ``_execute_bundle_atomically`` (US-005
        line 424), and converts the dict-shaped result into a
        ``BundleExecutionResult`` with one ``ExecutedAction`` per landed inner
        action.
        """

        def run(bundle: Bundle, slot: int) -> BundleExecutionResult:
            del slot
            actions = [a for tx in bundle.txs for a in tx.actions]
            outcome = self._execute_bundle_atomically(actions, round_num, ts)
            executed = [
                ExecutedAction(
                    action=a,
                    execution_cost=0,
                    cost_token=None,
                    succeeded=True,
                )
                for a in outcome["executed"]
            ]
            return BundleExecutionResult(
                reverted=outcome["reverted"],
                failed_at_index=outcome["failed_at_index"],
                failed_reason=outcome["failed_reason"],
                executed=executed,
            )

        return run

    def _emit_action_failed(
        self,
        action: Action,
        round_num: int,
        ts: int,
        error: str,
        *,
        execution_cost: Numeric | None = None,
        cost_token: TokenId | None = None,
    ) -> None:
        result = ExecutionResult(success=False, error=error)
        data: dict[str, Any] = {
            "agent_id": action.agent_id,
            "action": action,
            "result": result,
        }
        if execution_cost is not None:
            data.update(
                self._action_event_data(
                    action,
                    result,
                    execution_cost=execution_cost,
                    cost_token=cost_token,
                )
            )
        self._record_slot_failure(error)
        self._bus.emit(Event(
            type=EventType.ACTION_FAILED,
            round=round_num,
            timestamp=ts,
            data=data,
        ))

    def _record_slot_success(self) -> None:
        capture = self._slot_action_capture
        if capture is not None and capture.get("success") is None:
            capture["success"] = True

    def _record_slot_failure(self, reason: str) -> None:
        capture = self._slot_action_capture
        if capture is not None:
            capture["success"] = False
            capture["reason"] = reason

    def _emit_action_dropped(self, action: Action, round_num: int, ts: int, reason: str) -> None:
        result = ExecutionResult(success=False, error=reason)
        self._bus.emit(Event(
            type=EventType.ACTION_DROPPED,
            round=round_num,
            timestamp=ts,
            data={
                "agent_id": action.agent_id,
                "action": action,
                "reason": reason,
                "result": result,
            },
        ))
        # PRD US-004 line 368: stash per-agent drop reasons for the next
        # slot's DecisionContext.last_drop_reasons. Cleared in
        # _rotate_drop_reasons at the start of each slot.
        if action.agent_id:
            buckets = self._next_drop_reasons_by_agent.setdefault(
                action.agent_id, {}
            )
            buckets.setdefault(reason, []).append(action)

    def _action_event_data(
        self,
        action: Action,
        result: ExecutionResult,
        *,
        execution_cost: Numeric,
        cost_token: TokenId | None,
    ) -> dict[str, Any]:
        return {
            "agent_id": action.agent_id,
            "action": action,
            "result": result,
            "execution_cost": execution_cost,
            "gas_cost": execution_cost,
            "cost_token": cost_token,
        }

    def _route_fee_splits(self, result: ExecutionResult, market: Market | None) -> None:
        if not result.fee_splits or result.fee_token is None:
            return

        for destination, amount in result.fee_splits.items():
            per_token = self._round_fee_splits.setdefault(destination, {})
            per_token[result.fee_token] = per_token.get(result.fee_token, 0) + amount
            if destination == "lp":
                if market is not None:
                    per_market = self._pending_lp_fees.setdefault(id(market), {})
                    per_market[result.fee_token] = per_market.get(result.fee_token, 0) + amount
                continue
            token_balances = self._fee_destination_balances.setdefault(destination, {})
            token_balances[result.fee_token] = token_balances.get(result.fee_token, 0) + amount

    def _infer_weight_scale(self, market: Market) -> Numeric:
        if self._config.numeric_mode.use_float:
            return 1.0
        token_scale = self._infer_token_scale(market)
        return token_scale if token_scale is not None else 10**9

    def _infer_price_scale(self, market: Market) -> Numeric:
        if self._config.numeric_mode.use_float:
            return 1.0
        token_scale = self._infer_token_scale(market)
        return token_scale if token_scale is not None else 10**9

    @staticmethod
    def _infer_token_scale(market: Market) -> int | None:
        tokens = getattr(market, "_tokens", None)
        if tokens:
            first = tokens[0]
            return getattr(first, "scale", None)
        return None

    def _restore_market_reference(self, market: Market, restored_market: Market) -> None:
        if not self._is_world:
            self._market = restored_market
            return

        for name, current_market in self._market.markets.items():
            if current_market is market:
                self._market.markets[name] = restored_market
                return

    def _attach_feed_rngs(self) -> None:
        if not self._config.feeds:
            return
        for feed in self._config.feeds:
            attach_rng = getattr(feed, "set_rng", None)
            if callable(attach_rng):
                child_seed = int(self._feed_rng.integers(0, 2**32))
                attach_rng(np.random.default_rng(child_seed))

    def _configure_numeric_mode(self) -> None:
        def configure_market(market: Market) -> None:
            configure = getattr(market, "configure_numeric_mode", None)
            if callable(configure):
                configure(self._config.numeric_mode)

        if self._is_world:
            for market in self._market.markets.values():
                configure_market(market)
        else:
            configure_market(self._market)

        if self._config.numeric_mode.use_float:
            for agent in self._agents:
                agent.state.balances = {
                    token_id: float(balance)
                    for token_id, balance in agent.state.balances.items()
                }
                agent.state.cumulative_volume = float(agent.state.cumulative_volume)
                agent.state.realized_pnl = float(agent.state.realized_pnl)
        else:
            for agent in self._agents:
                agent.state.balances = {
                    token_id: int(balance)
                    for token_id, balance in agent.state.balances.items()
                }
                agent.state.cumulative_volume = int(agent.state.cumulative_volume)
                agent.state.realized_pnl = int(agent.state.realized_pnl)

    @staticmethod
    def _resolve_market_token(market: Market, attr_names: tuple[str, ...]) -> TokenId | None:
        for attr_name in attr_names:
            value = getattr(market, attr_name, None)
            if callable(value):
                value = value()
            if isinstance(value, str) and value:
                return value
        return None

    def _compute_round_feed_prices(self, round_num: int) -> dict[TokenId, Numeric] | None:
        if not self._config.feeds:
            return None

        if self._is_world:
            token_ids: set[TokenId] = set()
            for state in self._market.get_all_states().values():
                token_ids.update(state.tokens)
        else:
            token_ids = set(self._market.get_state().tokens)

        # PRD US-006 step 1.8b: feeds are no longer chain-neutral
        # ``get_price(token, round)`` consumers; project per-token views
        # through the ``OracleSource`` interface.
        prices: dict[TokenId, Numeric] = {}
        for feed in self._config.feeds:
            for token_id in token_ids:
                try:
                    price, _ = feed.oracle_for(token_id).price_at(round_num)
                except Exception:
                    continue
                prices[token_id] = price
        return prices
