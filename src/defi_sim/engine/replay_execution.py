"""``ReplayExecution`` mode (PRD US-002 line 280).

Feeds historical actions materialized from :class:`SlotSnapshot`s into the
engine, bypassing agent ``decide()`` calls. Counterfactual injection swaps
one parameter (tip, fee, ordering, agent strategy, ...) before the materialized
actions reach the BatchExecution admit/order/execute pipeline.

This module ships the skeleton called out at PRD line 280, the
counterfactual API shape at line 304, and the Diff API at line 321.
Artifact persistence (line 331) and HTTP surface (line 333) land in
follow-on iters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from defi_sim.core.agent import DecisionContext
from defi_sim.core.types import (
    Action,
    BundleOutcome,
    LPAction,
    LiquidateAction,
    MultiMarketAction,
)
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS
from defi_sim.engine.execution import BatchExecution
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.scheduler import Scheduler
from defi_sim.engine.slot import ExecutedAction, SlotContext, SlotOutcome
from defi_sim.metrics.replay import (
    compute_bundle_landing_rate,
    compute_cu_per_dollar_tip_breakeven_curve,
    compute_skip_rate_cost,
    compute_slot_inclusion_latency,
    compute_submission_path_comparison,
    compute_tip_efficiency,
    compute_write_lock_heatmap,
)

if TYPE_CHECKING:
    from defi_sim.core.agent import Agent
    from defi_sim.engine.bundle import Bundle
    from defi_sim_solana.replay.slot_client import SlotSnapshot


__all__ = [
    "AgentInjectCounterfactual",
    "Counterfactual",
    "CounterfactualSpec",
    "ErrorBand",
    "FeeReplaceCounterfactual",
    "OrderingReplaceCounterfactual",
    "ReplayDiff",
    "ReplayExecution",
    "RunSnapshot",
    "TipReplaceCounterfactual",
    "extract_actual_metrics",
    "run_snapshot_from_actions",
]


@dataclass(frozen=True)
class CounterfactualSpec:
    """Serializable description of a :class:`Counterfactual` (PRD line 331).

    Lives in the run artifact so a persisted replay can be inspected,
    re-played, or diffed without round-tripping the original Python object.
    ``kind`` is the counterfactual class name; ``params`` is a JSON-safe
    parameter dict.
    """

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": dict(self.params)}


class Counterfactual(ABC):
    """One-knob mutation applied to a slot's materialized action stream.

    Subclasses (PRD line 304) implement the per-flavor logic — tip replace,
    fee replace, ordering swap, synthetic-agent inject. The base class only
    pins the contract so :class:`ReplayExecution` can hold a heterogeneous
    list and apply each in submission order.
    """

    @abstractmethod
    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        """Return the post-mutation action list for ``slot``."""
        ...

    def to_spec(self) -> CounterfactualSpec:
        """Return a serializable description (PRD line 331).

        Default implementation reflects all non-private instance attributes
        whose values are JSON-safe scalars; subclasses with non-scalar
        targets override to project them into a JSON-safe form.
        """
        params: dict[str, Any] = {}
        for key, value in vars(self).items():
            if key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                params[key] = value
            else:
                params[key] = repr(value)
        return CounterfactualSpec(kind=type(self).__name__, params=params)


class ReplayExecution(BatchExecution):
    """Execution model that replays historical slots through the engine.

    The action queue is the *historical* action stream materialized from
    :class:`SlotSnapshot`s, not agent ``decide()`` output. ``step_slot``
    pulls the next snapshot, materializes it into engine actions, applies
    each registered counterfactual, and delegates to the BatchExecution
    admit/order/execute pipeline.
    """

    def __init__(
        self,
        slot_stream: Iterator["SlotSnapshot"],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._slot_stream = slot_stream
        self._counterfactuals: list[Counterfactual] = []
        self._last_replay_submitted_actions: list[Action] = []
        self._last_replay_actions: list[Action] = []
        self._last_replay_dropped: list[tuple[Action, str]] = []
        self._last_replay_diagnostics: list[Action] = []
        self._last_replay_outcome: SlotOutcome | None = None
        self._replay_state: Any | None = None

    def add_counterfactual(self, cf: Counterfactual) -> None:
        self._counterfactuals.append(cf)

    def bind_replay_state(self, state: Any) -> None:
        self._replay_state = state

    def supports_slot_execution(self) -> bool:
        return True

    def pending_actions_for_agent(
        self,
        agent: "Agent",
        pending: list[Action],
        round: int,
        context: OrderingContext | None = None,
    ) -> list[Action] | None:
        # Replay mode ignores agent decisions; actions come from the
        # historical stream, so no pending visibility is exposed.
        return []

    def execute_slot(self, ctx: SlotContext) -> SlotOutcome:
        """Replay one materialized slot through the engine executor callback."""

        from defi_sim_solana.replay.action_routing import (
            route_replay_actions_to_markets,
            unwrap_replay_action,
        )
        from defi_sim_solana.replay.materialize import materialize_slot

        historical = next(self._slot_stream)
        slot = historical.slot
        actions = materialize_slot(historical)
        state = self._replay_state if self._replay_state is not None else ctx
        for cf in self._counterfactuals:
            actions = cf.apply(actions, slot, state)

        routing = route_replay_actions_to_markets(actions)
        if ctx.ordering_context is not None:
            ctx.ordering_context.current_slot = slot
        admitted, dropped = self.admit(
            list(routing.executable),
            slot,
            ctx.ordering_context,
        )
        ordered = self.order(admitted, slot, ctx.ordering_context)
        executed = [ctx.executor(action, slot) for action in ordered]

        outcome = SlotOutcome(
            slot=slot,
            admitted=list(admitted),
            dropped=list(dropped),
            deferred=[],
            executed=executed,
        )
        self.on_slot_end(outcome)
        self._last_replay_submitted_actions = list(actions)
        self._last_replay_actions = [
            unwrap_replay_action(item.action) for item in executed
        ]
        self._last_replay_dropped = [
            (unwrap_replay_action(action), reason) for action, reason in dropped
        ]
        self._last_replay_diagnostics = list(routing.diagnostics)
        self._last_replay_outcome = outcome
        return outcome

    def step_slot(self, slot: int, state: Any) -> "SlotSnapshot":
        """Materialize the next historical slot, apply counterfactuals,
        and hand the resulting actions to the BatchExecution pipeline.
        """
        from defi_sim_solana.replay.materialize import materialize_slot

        historical = next(self._slot_stream)
        actions = materialize_slot(historical)
        for cf in self._counterfactuals:
            actions = cf.apply(actions, slot, state)

        ordering_context = OrderingContext(current_slot=slot)
        admitted, dropped = self.admit(actions, slot, ordering_context)
        ordered = self.order(admitted, slot, ordering_context)
        executed = [
            self._execute_replay_action(action, slot=slot, state=state)
            for action in ordered
        ]

        self.on_round_end(len(executed), slot)
        self._last_replay_submitted_actions = list(actions)
        self._last_replay_actions = [item.action for item in executed]
        self._last_replay_dropped = list(dropped)
        self._last_replay_diagnostics = []
        self._last_replay_outcome = SlotOutcome(
            slot=slot,
            admitted=list(admitted),
            dropped=list(dropped),
            deferred=[],
            executed=executed,
        )
        return historical

    def _execute_replay_action(
        self,
        action: Action,
        *,
        slot: int,
        state: Any,
    ) -> ExecutedAction:
        executor = getattr(state, "execute_replay_action", None)
        if callable(executor):
            executed = executor(action, slot)
            if isinstance(executed, ExecutedAction):
                return executed
            if isinstance(executed, bool):
                return ExecutedAction(
                    action=action,
                    execution_cost=self.cost(action, slot),
                    cost_token=self.cost_token(action),
                    succeeded=executed,
                    failure_reason=None if executed else "replay_executor_failed",
                )
        return ExecutedAction(
            action=action,
            execution_cost=self.cost(action, slot),
            cost_token=self.cost_token(action),
            succeeded=False,
            failure_reason="missing_replay_executor",
        )


class TipReplaceCounterfactual(Counterfactual):
    """Replace the tip lamports on a single bundle in the slot stream.

    Targets are matched on ``Action.bundle_id`` (when set by the
    materializer). Actions whose ``bundle_id`` does not match are passed
    through untouched. The full one-bundle-only behaviour test lands at
    PRD line 347.
    """

    def __init__(self, target_bundle_id: str, new_tip_lamports: int) -> None:
        self.target_bundle_id = target_bundle_id
        self.new_tip_lamports = int(new_tip_lamports)

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        del slot, state
        for action in actions:
            if getattr(action, "bundle_id", None) != self.target_bundle_id:
                continue
            if hasattr(action, "tip_lamports"):
                try:
                    action.tip_lamports = self.new_tip_lamports  # type: ignore[attr-defined]
                except (AttributeError, TypeError):
                    pass
        return actions

    def apply_to_bundles(self, bundles: list["Bundle"]) -> list["Bundle"]:
        """Mutate the matching bundle's ``tip_payments`` so its total tip
        becomes ``new_tip_lamports`` (PRD US-002 validation line 339).

        Match key is ``bundle.searcher_id == target_bundle_id``. Mutation
        happens AFTER ``Bundle.__post_init__`` so the resulting object is
        what the BundleAuction would observe in a counterfactual world —
        with ``new_tip_lamports=0`` the auction's ``admit`` step drops the
        bundle with ``BUNDLE_TIP_BELOW_MINIMUM``. The payment count stays
        stable so position-dependent semantics (PRD US-011 line 838) are
        preserved across the counterfactual.
        """
        for b in bundles:
            if getattr(b, "searcher_id", None) != self.target_bundle_id:
                continue
            if not b.tip_payments:
                continue
            if self.new_tip_lamports <= 0:
                for tp in b.tip_payments:
                    tp.lamports = 0
            else:
                b.tip_payments[0].lamports = self.new_tip_lamports
                for tp in b.tip_payments[1:]:
                    tp.lamports = 0
        return bundles


class FeeReplaceCounterfactual(Counterfactual):
    """Replace the fee bps for a target pool on the engine state.

    The mutation is applied to ``state`` (the simulation engine / world)
    rather than to the action stream — fees live on the market, not the
    action. Markets surface a configurable ``fee_bps`` attribute when
    they support fee replacement; markets that don't are left untouched.
    """

    def __init__(self, target_pool: str, new_fee_bps: int) -> None:
        self.target_pool = target_pool
        self.new_fee_bps = int(new_fee_bps)

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        del slot
        market = self._resolve_market(state)
        if market is not None and hasattr(market, "fee_bps"):
            try:
                market.fee_bps = self.new_fee_bps
            except (AttributeError, TypeError):
                pass
        return actions

    def _resolve_market(self, state: Any) -> Any | None:
        if state is None:
            return None
        markets = getattr(state, "markets", None)
        if isinstance(markets, dict):
            return markets.get(self.target_pool)
        market = getattr(state, "market", None)
        if market is not None and getattr(market, "name", None) == self.target_pool:
            return market
        return None


class OrderingReplaceCounterfactual(Counterfactual):
    """Replace the engine's scheduler with a counterfactual ordering.

    Swaps ``state.scheduler`` (or the equivalent attribute on the
    execution model) so the BatchExecution pipeline picks up the new
    scheduler on the next admit/order/execute pass.
    """

    def __init__(self, new_scheduler: Scheduler) -> None:
        self.new_scheduler = new_scheduler

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        del slot
        if state is not None:
            try:
                state.scheduler = self.new_scheduler
            except (AttributeError, TypeError):
                pass
        return actions


class AgentInjectCounterfactual(Counterfactual):
    """Inject a synthetic agent's actions into the materialized stream.

    The agent's ``decide`` is called with a minimal DecisionContext built
    from the current slot and state; the returned actions are appended to
    the historical stream. The full coverage test lands at PRD line 348.
    """

    def __init__(self, agent: "Agent") -> None:
        self.agent = agent

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        ctx = DecisionContext(
            current_round=slot,
            current_slot=slot,
            agent_state=getattr(self.agent, "state", DecisionContext().agent_state),
        )
        injected = list(self.agent.decide(ctx))
        return list(actions) + injected


# ----- Diff API (PRD line 321) -------------------------------------------------


@dataclass(frozen=True)
class ErrorBand:
    """Per-metric predicted-vs-actual residual for a replayed slot.

    ``supported`` is ``False`` when the actual side could not be extracted
    (e.g. the protocol decoder for that metric has not landed yet); such
    bands carry the predicted value but ``abs_error`` / ``rel_error`` are
    ``None`` and the band is excluded from any aggregate accuracy claim.
    """

    metric: str
    predicted: float
    actual: float | None
    abs_error: float | None
    rel_error: float | None
    supported: bool = True


@dataclass
class RunSnapshot:
    """Predicted-side metrics produced by a :class:`ReplayExecution` run.

    Mirrors the metric set called out at PRD line 329 — pool price post-slot,
    LP balance per agent, total volume, liquidations triggered, tips paid.
    ``unsupported_instruction_coverage`` carries the count of historical
    instructions whose protocol decoder has not yet landed; those instructions
    are excluded from model-vs-mainnet error bands.
    """

    pool_prices: dict[str, float] = field(default_factory=dict)
    lp_balances: dict[str, float] = field(default_factory=dict)
    total_volume: float = 0.0
    liquidations_triggered: int = 0
    tips_paid: int = 0
    unsupported_instruction_coverage: int = 0

    # Per-metric input buckets feeding the seven replay calculators
    # (PRD US-006 line 989 — snapshot surfaces metrics.replay).
    bundle_outcomes: list[BundleOutcome] = field(default_factory=list)
    tip_efficiency_samples: list[tuple[int, int]] = field(default_factory=list)
    slot_inclusion_samples: list[tuple[int, int]] = field(default_factory=list)
    breakeven_samples: list[tuple[int, int]] = field(default_factory=list)
    skip_rate_samples: list[tuple[bool, int]] = field(default_factory=list)
    write_lock_claims: list[tuple[str, int]] = field(default_factory=list)
    submission_path_samples: list[tuple[str, bool]] = field(default_factory=list)

    decoded_swap_count: int = 0
    decoded_liquidation_count: int = 0
    decoded_lp_action_count: int = 0

    def replay_metrics(self) -> dict[str, dict[str, Any]]:
        """Compute and serialize the seven PRD US-006 replay metrics.

        Returns one entry per metric keyed by the calculator name. Each entry
        is a JSON-safe dict with the headline scalar plus the metric's
        distribution-specific fields (latency percentiles, scatter axes,
        per-cell counts, ...). Empty input buckets yield zero-sample
        sentinels — see each calculator's empty-input contract.
        """

        landing_rate = compute_bundle_landing_rate(self.bundle_outcomes)
        tip_efficiency = compute_tip_efficiency(self.tip_efficiency_samples)
        latency = compute_slot_inclusion_latency(self.slot_inclusion_samples)
        breakeven = compute_cu_per_dollar_tip_breakeven_curve(self.breakeven_samples)
        skip_cost = compute_skip_rate_cost(self.skip_rate_samples)
        heatmap = compute_write_lock_heatmap(self.write_lock_claims)
        submission = compute_submission_path_comparison(self.submission_path_samples)

        return {
            landing_rate.name: {
                "value": landing_rate.value,
                "unit": landing_rate.unit,
                "sample_size": landing_rate.sample_size,
            },
            tip_efficiency.name: {
                "value": tip_efficiency.value,
                "unit": tip_efficiency.unit,
                "sample_size": tip_efficiency.sample_size,
            },
            latency.name: {
                "value": latency.headline.value,
                "unit": latency.unit,
                "sample_size": latency.sample_size,
                "mean": latency.mean,
                "median": latency.median,
                "p95": latency.p95,
                "p99": latency.p99,
                "samples": list(latency.samples),
            },
            breakeven.name: {
                "value": breakeven.headline.value,
                "unit": breakeven.unit,
                "sample_size": breakeven.sample_size,
                "tips": list(breakeven.tips),
                "extracted_values": list(breakeven.extracted_values),
                "ratios": list(breakeven.ratios),
            },
            skip_cost.name: {
                "value": skip_cost.value,
                "unit": skip_cost.unit,
                "sample_size": skip_cost.sample_size,
            },
            heatmap.name: {
                "value": heatmap.headline.value,
                "unit": heatmap.unit,
                "sample_size": heatmap.sample_size,
                "accounts": list(heatmap.accounts),
                "slots": list(heatmap.slots),
                "counts": [
                    {"account": account, "slot": slot, "count": count}
                    for (account, slot), count in heatmap.counts.items()
                ],
                "max_contention": heatmap.max_contention,
            },
            submission.name: {
                "value": submission.headline.value,
                "unit": submission.unit,
                "sample_size": submission.sample_size,
                "paths": list(submission.paths),
                "submitted": list(submission.submitted),
                "landed": list(submission.landed),
                "landing_rates": list(submission.landing_rates),
                "spread": submission.spread,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the snapshot, surfacing replay metrics under ``metrics.replay``.

        PRD US-006 line 989 requires that a replay run snapshot includes all
        seven metric calculators' results under ``metrics.replay``. The flat
        scalar fields (tips_paid, total_volume, liquidations_triggered, ...)
        remain at the top level for callers that only need the predicted-side
        diff inputs.
        """
        return {
            "pool_prices": dict(self.pool_prices),
            "lp_balances": dict(self.lp_balances),
            "total_volume": self.total_volume,
            "liquidations_triggered": self.liquidations_triggered,
            "tips_paid": self.tips_paid,
            "unsupported_instruction_coverage": self.unsupported_instruction_coverage,
            "metrics": {"replay": self.replay_metrics()},
        }


def extract_actual_metrics(actual: "SlotSnapshot") -> RunSnapshot:
    """Pull mainnet-actual metrics from a :class:`SlotSnapshot`.

    Actual-side extraction goes through the same materialized action
    vocabulary as predicted replay. Raw Jito tips are still folded in when
    no decoded tip action is present so older corpus fixtures keep their
    historical tip signal, but decoded swaps, liquidations, LP actions, and
    canonical replay chart inputs are populated from protocol actions.
    """

    from defi_sim_solana.replay.materialize import materialize_slot

    actions = materialize_slot(actual)
    snapshot = run_snapshot_from_actions(actions, slot=actual.slot)
    tips = 0
    for tip in getattr(actual, "jito_tips", ()) or ():
        if not isinstance(tip, dict):
            continue
        amount = tip.get("lamports") or tip.get("amount") or 0
        try:
            tips += int(amount)
        except (TypeError, ValueError):
            continue
    if tips and snapshot.tips_paid == 0:
        snapshot.tips_paid = tips
    return snapshot


def run_snapshot_from_actions(actions: list[Action], *, slot: int) -> RunSnapshot:
    """Derive replay metrics from materialized or executed engine actions."""

    from defi_sim_solana.replay.materialize import (
        ActionDecodeStatus,
        MaterializedSwapAction,
        TokenTransferAction,
        action_decode_status,
    )

    pool_prices: dict[str, float] = {}
    lp_balances: dict[str, float] = {}
    total_volume = 0.0
    liquidations_triggered = 0
    tips_paid = 0
    unsupported = 0
    decoded_swap_count = 0
    decoded_liquidation_count = 0
    decoded_lp_action_count = 0
    bundle_outcomes: list[BundleOutcome] = []
    tip_efficiency_samples: list[tuple[int, int]] = []
    slot_inclusion_samples: list[tuple[int, int]] = []
    breakeven_samples: list[tuple[int, int]] = []
    skip_rate_samples: list[tuple[bool, int]] = []
    write_lock_claims: list[tuple[str, int]] = []
    submission_path_samples: list[tuple[str, bool]] = []

    for raw_action in actions:
        action = (
            raw_action.inner
            if isinstance(raw_action, MultiMarketAction)
            else raw_action
        )
        decode_status = action_decode_status(action)
        landed = decode_status is ActionDecodeStatus.DECODED
        if not landed:
            unsupported += 1

        submission_path_samples.append((action.submission_path, landed))
        for account in getattr(action, "write_locks", frozenset()):
            write_lock_claims.append((str(account), slot))

        if isinstance(action, TokenTransferAction):
            for account in (action.source, action.destination):
                if account:
                    write_lock_claims.append((account, slot))

        if isinstance(action, MaterializedSwapAction):
            amount_in = float(action.amount_in or 0)
            total_volume += amount_in
            if landed:
                decoded_swap_count += 1
            if action.pool_id:
                write_lock_claims.append((action.pool_id, slot))
            for account in action.pool_reserve_accounts:
                write_lock_claims.append((account, slot))
            if action.pool_id and action.amount_out is not None and amount_in:
                pool_prices[action.pool_id] = float(action.amount_out) / amount_in

        if isinstance(action, LiquidateAction):
            liquidations_triggered += 1
            if landed:
                decoded_liquidation_count += 1

        if isinstance(action, LPAction):
            amount = float(getattr(action, "amount", 0) or 0)
            agent_id = str(action.agent_id)
            lp_balances[agent_id] = lp_balances.get(agent_id, 0.0) + amount
            if landed:
                decoded_lp_action_count += 1

        tip_lamports = getattr(action, "tip_lamports", None)
        if isinstance(tip_lamports, int):
            tips_paid += tip_lamports
            bundle_id = getattr(action, "bundle_id", None)
            if bundle_id:
                bundle_landed = landed and tip_lamports >= MIN_BUNDLE_TIP_LAMPORTS
                bundle_outcomes.append(
                    BundleOutcome(
                        slot=slot,
                        bundle_index=len(bundle_outcomes),
                        status="landed" if bundle_landed else "dropped",
                        tip_lamports=tip_lamports,
                        validator_revenue_lamports=tip_lamports
                        if bundle_landed
                        else 0,
                        stake_pool_revenue_lamports=0,
                        num_txs=1,
                        total_cu=int(action.compute_unit_limit or 0),
                        drop_reason=None
                        if bundle_landed
                        else "bundle_tip_below_minimum",
                    )
                )
                if bundle_landed:
                    submitted_slot = slot
                    metadata = getattr(action, "materialized_metadata", None)
                    metadata_slot = getattr(metadata, "slot", None)
                    if isinstance(metadata_slot, int):
                        submitted_slot = metadata_slot
                    slot_inclusion_samples.append((submitted_slot, slot))
                    ev = getattr(action, "extracted_value_lamports", None)
                    if isinstance(ev, int) and ev > 0:
                        tip_efficiency_samples.append((tip_lamports, ev))
                        breakeven_samples.append((tip_lamports, ev))

    if actions:
        skip_rate_samples.append((False, int(tips_paid + total_volume)))

    return RunSnapshot(
        pool_prices=pool_prices,
        lp_balances=lp_balances,
        total_volume=total_volume,
        liquidations_triggered=liquidations_triggered,
        tips_paid=tips_paid,
        unsupported_instruction_coverage=unsupported,
        bundle_outcomes=bundle_outcomes,
        tip_efficiency_samples=tip_efficiency_samples,
        slot_inclusion_samples=slot_inclusion_samples,
        breakeven_samples=breakeven_samples,
        skip_rate_samples=skip_rate_samples,
        write_lock_claims=write_lock_claims,
        submission_path_samples=submission_path_samples,
        decoded_swap_count=decoded_swap_count,
        decoded_liquidation_count=decoded_liquidation_count,
        decoded_lp_action_count=decoded_lp_action_count,
    )


class ReplayDiff:
    """Predicted-vs-actual diff for a replayed slot (PRD line 321).

    Holds the predicted-side :class:`RunSnapshot` and the actual
    :class:`SlotSnapshot` or pre-aggregated :class:`RunSnapshot`;
    ``per_metric_error`` returns one
    :class:`ErrorBand` per metric covered for decoded protocols.
    Metrics whose actual extraction is gated on an unlanded decoder are
    emitted with ``supported=False`` so callers can distinguish "matched
    within band" from "not yet measurable".
    """

    _METRICS: tuple[str, ...] = (
        "bundle_landing_rate",
        "tip_efficiency",
        "slot_inclusion_latency",
        "cu_per_dollar_tip_breakeven",
        "skip_rate_cost",
        "write_lock_heatmap",
        "submission_path_comparison",
        "pool_price",
        "lp_balance",
        "total_volume",
        "liquidations_triggered",
        "tips_paid",
    )

    def __init__(
        self,
        predicted: RunSnapshot,
        actual: "SlotSnapshot | RunSnapshot",
    ) -> None:
        self.predicted = predicted
        self.actual = actual
        self._actual_metrics = (
            actual if isinstance(actual, RunSnapshot) else extract_actual_metrics(actual)
        )

    @property
    def unsupported_instruction_coverage(self) -> int:
        return self._actual_metrics.unsupported_instruction_coverage

    def per_metric_error(self) -> dict[str, ErrorBand]:
        out: dict[str, ErrorBand] = {}
        out.update(self._canonical_metric_bands())
        # Scalar metrics measurable from a raw SlotSnapshot today: tips_paid.
        out["tips_paid"] = self._scalar_band(
            "tips_paid",
            float(self.predicted.tips_paid),
            float(self._actual_metrics.tips_paid),
            supported=True,
        )
        out["total_volume"] = self._scalar_band(
            "total_volume",
            float(self.predicted.total_volume),
            float(self._actual_metrics.total_volume),
            supported=(
                self._actual_metrics.total_volume != 0
                or self._actual_metrics.decoded_swap_count > 0
            ),
        )
        out["liquidations_triggered"] = self._scalar_band(
            "liquidations_triggered",
            float(self.predicted.liquidations_triggered),
            float(self._actual_metrics.liquidations_triggered),
            supported=(
                self._actual_metrics.liquidations_triggered != 0
                or self._actual_metrics.decoded_liquidation_count > 0
            ),
        )
        for pool in sorted(
            set(self.predicted.pool_prices) | set(self._actual_metrics.pool_prices)
        ):
            price = self.predicted.pool_prices.get(pool, 0.0)
            actual = self._actual_metrics.pool_prices.get(pool)
            out[f"pool_price:{pool}"] = self._scalar_band(
                f"pool_price:{pool}",
                float(price),
                float(actual) if actual is not None else None,
                supported=actual is not None,
            )
        for agent_id in sorted(
            set(self.predicted.lp_balances) | set(self._actual_metrics.lp_balances)
        ):
            balance = self.predicted.lp_balances.get(agent_id, 0.0)
            actual = self._actual_metrics.lp_balances.get(agent_id)
            out[f"lp_balance:{agent_id}"] = self._scalar_band(
                f"lp_balance:{agent_id}",
                float(balance),
                float(actual) if actual is not None else None,
                supported=actual is not None,
            )
        return out

    def _canonical_metric_bands(self) -> dict[str, ErrorBand]:
        predicted = self.predicted.replay_metrics()
        actual = self._actual_metrics.replay_metrics()
        out: dict[str, ErrorBand] = {}
        for metric in self._METRICS[:7]:
            predicted_value = _metric_value(predicted.get(metric))
            actual_value = _metric_value(actual.get(metric))
            out[metric] = self._scalar_band(
                metric,
                predicted_value,
                actual_value,
                supported=True,
            )
        return out

    @staticmethod
    def _scalar_band(
        metric: str,
        predicted: float,
        actual: float | None,
        *,
        supported: bool,
    ) -> ErrorBand:
        if actual is None or not supported:
            return ErrorBand(
                metric=metric,
                predicted=predicted,
                actual=actual,
                abs_error=None,
                rel_error=None,
                supported=supported,
            )
        abs_error = abs(predicted - actual)
        rel_error = abs_error / abs(actual) if actual != 0 else None
        return ErrorBand(
            metric=metric,
            predicted=predicted,
            actual=actual,
            abs_error=abs_error,
            rel_error=rel_error,
            supported=True,
        )


def _metric_value(metric: dict[str, Any] | None) -> float:
    if not isinstance(metric, dict):
        return 0.0
    value = metric.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
