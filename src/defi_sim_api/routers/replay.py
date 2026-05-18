"""Replay endpoints (PRD US-002 line 333).

``POST /v1/replay`` accepts a slot range + counterfactual list, materializes
each slot in the range (corpus-first via ``get_slot``), and persists the run
as a first-class artifact via :func:`persist_replay_run`. The response carries
the run ID plus the metadata called out at PRD line 333: decoded transaction
share, unsupported program IDs, and whether the run is eligible for
calibration assertions.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status

from defi_sim_api.auth import User, current_user
from defi_sim_api.routers._ownership import owner_for_create
from pydantic import BaseModel, Field

from defi_sim.calibration.thresholds import Threshold, load_thresholds
from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.clock import SolanaSlotClock
from defi_sim.core.market import Market
from defi_sim.core.types import (
    Action,
    AgentState,
    BundleOutcome,
    ExecutionContext,
    ExecutionResult,
    LPAction,
    LiquidateAction,
    MarketSnapshot,
)
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.replay_execution import (
    Counterfactual,
    CounterfactualSpec,
    FeeReplaceCounterfactual,
    ReplayDiff,
    ReplayExecution,
    RunSnapshot,
    extract_actual_metrics,
    TipReplaceCounterfactual,
)
from defi_sim.engine.slot import ExecutedAction
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.world import World
from defi_sim_api import state
from defi_sim_api.backend.runtime import persist_replay_run
from defi_sim_solana.replay.action_routing import (
    REPLAY_ACCOUNTING_MARKET,
    replay_market_name_for_action,
    unwrap_replay_action,
)
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    MaterializedActionMetadata,
    MaterializedSwapAction,
    OpaqueAction,
    TipAction,
    TokenTransferAction,
    action_decode_status,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot, get_slot

router = APIRouter(prefix="/v1/replay", tags=["replay"])


# JSON-decodable counterfactual subclasses.
_DECODABLE_CFS: dict[str, type[Counterfactual]] = {
    "TipReplaceCounterfactual": TipReplaceCounterfactual,
    "FeeReplaceCounterfactual": FeeReplaceCounterfactual,
}
_SCHEDULER_TYPES = {"serial", "priority", "custom"}
_JITO_SEARCHER_STRATEGIES = {"backrun", "sandwich"}
_REPLAY_SLOT_SUMMARY_CACHE_MAX = 512


@dataclass(frozen=True)
class _ReplaySlotSummary:
    slots_loaded: int
    total_actions: int
    decoded_actions: int
    unsupported_program_ids: tuple[str, ...]


@dataclass(frozen=True)
class _LoadedReplaySlot:
    snapshot: SlotSnapshot
    summary: _ReplaySlotSummary


@dataclass(frozen=True)
class _ReplayRangeSummary:
    slots_loaded: int
    total_actions: int
    decoded_actions: int
    unsupported_program_ids: tuple[str, ...]

    @property
    def decoded_transaction_share(self) -> float:
        if not self.total_actions:
            return 0.0
        return self.decoded_actions / self.total_actions


_REPLAY_SLOT_SUMMARY_CACHE: OrderedDict[int, _LoadedReplaySlot] = OrderedDict()
_REPLAY_SLOT_SUMMARY_LOCK = Lock()

# Manifest categories that mark a corpus slot as a placeholder/development
# fixture (see solana-plans/calibration/corpus/420196842/manifest.yaml).
# These slots are reachable for replay but never support a mainnet-accuracy
# claim, regardless of decoded coverage.
_CORPUS_SYNTHETIC_CATEGORIES: frozenset[str] = frozenset({"synthetic"})


def _corpus_slot_category(slot: int) -> str | None:
    """Return the manifest.yaml ``category`` for a corpus slot, or ``None``.

    ``None`` means the slot is not in the calibration corpus (no committed
    manifest), so it cannot back a mainnet-accuracy claim. A returned
    category of ``synthetic`` marks placeholder/development fixtures that
    are also not eligible.
    """
    manifest_path = corpus_root() / str(slot) / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    category = data.get("category")
    return str(category) if isinstance(category, str) else None


def _all_slots_corpus_calibrated(start: int, end: int) -> bool:
    for slot in range(start, end + 1):
        category = _corpus_slot_category(slot)
        if category is None or category in _CORPUS_SYNTHETIC_CATEGORIES:
            return False
    return True


def _diff_within_thresholds(diff_payload: dict[str, Any] | None) -> bool:
    """Return ``True`` iff every comparable per-metric error band passes.

    A band is "comparable" when it has a non-null ``actual``, is supported,
    and has a configured threshold. Bands without an actual or threshold
    are skipped — they cannot prove or disprove the claim. The result is
    ``False`` when no comparable band exists, so a vacuous pass cannot
    produce a mainnet-accuracy claim.
    """
    if not diff_payload:
        return False
    bands = diff_payload.get("per_metric_error", {})
    if not isinstance(bands, dict) or not bands:
        return False
    saw_compared = False
    for band in bands.values():
        if not isinstance(band, dict):
            continue
        if band.get("actual") is None or not band.get("supported", True):
            continue
        threshold = band.get("threshold")
        if threshold is None:
            continue
        if band.get("threshold_kind") == "relative":
            err = band.get("rel_error")
        else:
            err = band.get("abs_error")
        if err is None:
            continue
        if err > threshold:
            return False
        saw_compared = True
    return saw_compared


class _ExecutableOrderingCounterfactual(Counterfactual):
    def __init__(
        self,
        *,
        scheduler_type: str,
        signature_order: tuple[str, ...] = (),
    ) -> None:
        self.scheduler_type = scheduler_type
        self.signature_order = signature_order

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        del slot, state
        if self.scheduler_type == "priority":
            return sorted(
                actions,
                key=lambda action: action.priority_lamports(),
                reverse=True,
            )
        if self.scheduler_type == "custom" and self.signature_order:
            rank = {
                signature: index for index, signature in enumerate(self.signature_order)
            }
            indexed = list(enumerate(actions))
            indexed.sort(
                key=lambda item: (
                    rank.get(_action_signature(item[1]) or "", len(rank)),
                    item[0],
                )
            )
            return [action for _, action in indexed]
        return list(actions)

    def to_spec(self) -> CounterfactualSpec:
        scheduler: dict[str, Any] = {"type": self.scheduler_type}
        if self.signature_order:
            scheduler["signature_order"] = list(self.signature_order)
        return CounterfactualSpec(
            kind="OrderingReplaceCounterfactual",
            params={"scheduler": scheduler},
        )


class _ExecutableAgentInjectCounterfactual(Counterfactual):
    def __init__(
        self,
        *,
        agent_id: str,
        strategy: str,
        min_ev_to_submit_lamports: int,
        tip_account: str,
    ) -> None:
        self.agent_id = agent_id
        self.strategy = strategy
        self.min_ev_to_submit_lamports = int(min_ev_to_submit_lamports)
        self.tip_account = tip_account

    def apply(
        self,
        actions: list[Action],
        slot: int,
        state: Any,
    ) -> list[Action]:
        del state
        tip_lamports = max(MIN_BUNDLE_TIP_LAMPORTS, self.min_ev_to_submit_lamports)
        bundle_id = f"{self.agent_id}:{self.strategy}:{slot}"
        injected = TipAction(
            agent_id=self.agent_id,
            compute_unit_limit=0,
            submission_path="jito_relayer",
            recipient=self.tip_account,
            tip_lamports=tip_lamports,
            signature=f"synthetic-agent-inject:{bundle_id}",
            bundle_id=bundle_id,
            materialized_metadata=MaterializedActionMetadata(
                decode_status=ActionDecodeStatus.DECODED,
                signature=f"synthetic-agent-inject:{bundle_id}",
                slot=slot,
                bundle_id=bundle_id,
            ),
        )
        injected.extracted_value_lamports = max(  # type: ignore[attr-defined]
            tip_lamports,
            self.min_ev_to_submit_lamports,
        )
        return [*actions, injected]

    def to_spec(self) -> CounterfactualSpec:
        return CounterfactualSpec(
            kind="AgentInjectCounterfactual",
            params={
                "agent_type": "jito_searcher",
                "agent_id": self.agent_id,
                "strategy": self.strategy,
                "min_ev_to_submit_lamports": self.min_ev_to_submit_lamports,
                "tip_account": self.tip_account,
            },
        )


@dataclass
class _ReplayMarketSnapshot(MarketSnapshot):
    price: float | None = None
    total_volume: float = 0.0
    slot_volume: float = 0.0
    fee_bps: int = 0
    last_slot: int | None = None


@dataclass
class _ReplayEngineState:
    markets: dict[str, Market]


class _ReplayAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id, balances={"COLLATERAL": 10**30})

    def decide(self, ctx: DecisionContext) -> list[Action]:
        del ctx
        return []


class _ReplayProtocolMarket(Market):
    market_type = "replay_protocol"

    def __init__(self, name: str) -> None:
        self.name = name
        self.fee_bps = 0
        self.total_volume = 0.0
        self.last_slot_volume = 0.0
        self.last_price: float | None = None
        self.last_slot: int | None = None
        self.executed: list[Action] = []

    def get_state(self) -> _ReplayMarketSnapshot:
        return _ReplayMarketSnapshot(
            num_assets=0,
            tokens=[],
            price=self.last_price,
            total_volume=self.total_volume,
            slot_volume=self.last_slot_volume,
            fee_bps=self.fee_bps,
            last_slot=self.last_slot,
        )

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.executed.append(action)
        current_slot = int(ctx.current_round)
        if self.last_slot != current_slot:
            self.last_slot_volume = 0.0
        self.last_slot = current_slot
        if isinstance(action, MaterializedSwapAction):
            amount_in = float(action.amount_in or 0)
            amount_out = (
                float(action.amount_out or 0)
                if action.amount_out is not None
                else None
            )
            if amount_out is not None and self.fee_bps:
                amount_out = amount_out * max(0, 10_000 - self.fee_bps) / 10_000
            if amount_out is not None and amount_in:
                self.last_price = amount_out / amount_in
            self.total_volume += amount_in
            self.last_slot_volume += amount_in
            return ExecutionResult(success=True, volume=amount_in)
        return ExecutionResult(success=True)

    def copy(self) -> "_ReplayProtocolMarket":
        copied = _ReplayProtocolMarket(self.name)
        copied.fee_bps = self.fee_bps
        copied.total_volume = self.total_volume
        copied.last_slot_volume = self.last_slot_volume
        copied.last_price = self.last_price
        copied.last_slot = self.last_slot
        copied.executed = list(self.executed)
        return copied

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "name": self.name,
                "fee_bps": self.fee_bps,
                "total_volume": self.total_volume,
                "last_slot_volume": self.last_slot_volume,
                "last_price": self.last_price,
                "last_slot": self.last_slot,
            }
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "_ReplayProtocolMarket":
        raw = json.loads(data.decode("utf-8"))
        market = cls(str(raw["name"]))
        market.fee_bps = int(raw.get("fee_bps", 0))
        market.total_volume = float(raw.get("total_volume", 0.0))
        market.last_slot_volume = float(raw.get("last_slot_volume", 0.0))
        price = raw.get("last_price")
        market.last_price = float(price) if price is not None else None
        last_slot = raw.get("last_slot")
        market.last_slot = int(last_slot) if last_slot is not None else None
        return market


class _ReplayAccountingMarket(_ReplayProtocolMarket):
    market_type = "replay_accounting"

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        self.executed.append(action)
        current_slot = int(ctx.current_round)
        if self.last_slot != current_slot:
            self.last_slot_volume = 0.0
        self.last_slot = current_slot
        if isinstance(action, TipAction):
            landed = action.tip_lamports >= MIN_BUNDLE_TIP_LAMPORTS
            return ExecutionResult(
                success=landed,
                error=None if landed else "bundle_tip_below_minimum",
            )
        return ExecutionResult(success=True)


class CounterfactualSpecRequest(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class ReplayRequest(BaseModel):
    slot_range: tuple[int, int]
    counterfactuals: list[CounterfactualSpecRequest] = Field(default_factory=list)
    seed: int | None = None


class ReplayResponse(BaseModel):
    run_id: str
    slot_range: tuple[int, int]
    slots_loaded: int
    counterfactuals: list[dict[str, Any]]
    decoded_transaction_share: float
    unsupported_program_ids: list[str]
    eligible_for_calibration: bool
    # PRD US-002 validation line 338: decoded coverage is diagnostic until
    # committed calibration evidence exists; development and partial replays
    # keep ``mainnet_accuracy_claim`` false.
    replay_kind: str
    mainnet_accuracy_claim: bool


class ReplayBundleTarget(BaseModel):
    bundle_id: str
    tip_lamports: int
    num_actions: int


class ReplayPoolTarget(BaseModel):
    pool_id: str
    decoded_swaps: int


class ReplayTargetsResponse(BaseModel):
    slot: int
    bundles: list[ReplayBundleTarget]
    pools: list[ReplayPoolTarget]


def _bad_counterfactual(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _decode_ordering_counterfactual(params: dict[str, Any]) -> Counterfactual:
    scheduler = params.get("scheduler")
    if scheduler is None:
        scheduler = {"type": params.get("scheduler_type", "serial")}
    if not isinstance(scheduler, dict):
        raise _bad_counterfactual(
            "OrderingReplaceCounterfactual.scheduler must be an object"
        )
    scheduler_type = str(scheduler.get("type", "serial"))
    if scheduler_type not in _SCHEDULER_TYPES:
        raise _bad_counterfactual(
            "OrderingReplaceCounterfactual.scheduler.type must be one of "
            f"{sorted(_SCHEDULER_TYPES)}"
        )
    signature_order = scheduler.get("signature_order", scheduler.get("order", ()))
    if signature_order is None:
        signature_order = ()
    if not isinstance(signature_order, (list, tuple)):
        raise _bad_counterfactual(
            "OrderingReplaceCounterfactual.scheduler.signature_order must be an array"
        )
    normalized_order = tuple(str(signature) for signature in signature_order)
    return _ExecutableOrderingCounterfactual(
        scheduler_type=scheduler_type,
        signature_order=normalized_order,
    )


def _decode_agent_counterfactual(params: dict[str, Any]) -> Counterfactual:
    agent_type = str(params.get("agent_type", "jito_searcher"))
    if agent_type != "jito_searcher":
        raise _bad_counterfactual(
            "AgentInjectCounterfactual.agent_type must be 'jito_searcher'"
        )
    strategy = str(params.get("strategy", "backrun"))
    if strategy not in _JITO_SEARCHER_STRATEGIES:
        raise _bad_counterfactual(
            "AgentInjectCounterfactual.strategy must be one of "
            f"{sorted(_JITO_SEARCHER_STRATEGIES)}"
        )
    agent_id = str(params.get("agent_id", "jito-searcher-cf")).strip()
    tip_account = str(params.get("tip_account", "")).strip()
    if not agent_id:
        raise _bad_counterfactual("AgentInjectCounterfactual.agent_id is required")
    if not tip_account:
        raise _bad_counterfactual("AgentInjectCounterfactual.tip_account is required")
    try:
        min_ev = int(params.get("min_ev_to_submit_lamports", 0))
    except (TypeError, ValueError) as exc:
        raise _bad_counterfactual(
            "AgentInjectCounterfactual.min_ev_to_submit_lamports must be an integer"
        ) from exc
    if min_ev < 0:
        raise _bad_counterfactual(
            "AgentInjectCounterfactual.min_ev_to_submit_lamports must be non-negative"
        )
    return _ExecutableAgentInjectCounterfactual(
        agent_id=agent_id,
        strategy=strategy,
        min_ev_to_submit_lamports=min_ev,
        tip_account=tip_account,
    )


def _decode_counterfactual(spec: CounterfactualSpecRequest) -> Any:
    projected = ("OrderingReplaceCounterfactual", "AgentInjectCounterfactual")
    if spec.kind == "OrderingReplaceCounterfactual":
        return _decode_ordering_counterfactual(spec.params)
    if spec.kind == "AgentInjectCounterfactual":
        return _decode_agent_counterfactual(spec.params)
    cls = _DECODABLE_CFS.get(spec.kind)
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"counterfactual kind {spec.kind!r} is not decodable from JSON; "
                f"supported: {sorted([*_DECODABLE_CFS, *projected])}"
            ),
        )
    try:
        return cls(**spec.params)
    except TypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid params for {spec.kind}: {exc}",
        ) from exc


def _load_slot(slot: int) -> SlotSnapshot | None:
    """Load a slot from corpus / configured client; return None on miss.

    The HTTP surface stays usable in environments where only a subset of the
    requested range is available (e.g. CI with only entry-gate fixtures).
    """
    try:
        return get_slot(slot)
    except (LookupError, RuntimeError):
        return None


def clear_replay_cache() -> None:
    """Clear cached per-slot replay summaries.

    Used by tests and useful for long-lived dev servers after corpus fixtures
    change on disk. Missing slots are intentionally not cached, so adding a
    fixture or wiring RPC in the same process can still take effect.
    """
    with _REPLAY_SLOT_SUMMARY_LOCK:
        _REPLAY_SLOT_SUMMARY_CACHE.clear()


def _cached_loaded_slot(slot: int) -> _LoadedReplaySlot | None:
    with _REPLAY_SLOT_SUMMARY_LOCK:
        loaded = _REPLAY_SLOT_SUMMARY_CACHE.get(slot)
        if loaded is None:
            return None
        _REPLAY_SLOT_SUMMARY_CACHE.move_to_end(slot)
        return loaded


def _store_loaded_slot(slot: int, loaded: _LoadedReplaySlot) -> None:
    if loaded.summary.slots_loaded == 0:
        return
    with _REPLAY_SLOT_SUMMARY_LOCK:
        _REPLAY_SLOT_SUMMARY_CACHE[slot] = loaded
        _REPLAY_SLOT_SUMMARY_CACHE.move_to_end(slot)
        while len(_REPLAY_SLOT_SUMMARY_CACHE) > _REPLAY_SLOT_SUMMARY_CACHE_MAX:
            _REPLAY_SLOT_SUMMARY_CACHE.popitem(last=False)


def _summarize_loaded_slot(snapshot: SlotSnapshot) -> _ReplaySlotSummary:
    actions = materialize_slot(snapshot)
    decoded_actions = 0
    unsupported: dict[str, None] = {}
    for action in actions:
        status = action_decode_status(action)
        if status is ActionDecodeStatus.DECODED:
            decoded_actions += 1
        else:
            metadata = getattr(action, "materialized_metadata", None)
            program_ids = getattr(metadata, "unsupported_program_ids", ())
            if not program_ids and isinstance(action, OpaqueAction):
                program_ids = action.program_ids
            for pid in program_ids:
                unsupported.setdefault(pid, None)
    return _ReplaySlotSummary(
        slots_loaded=1,
        total_actions=len(actions),
        decoded_actions=decoded_actions,
        unsupported_program_ids=tuple(unsupported.keys()),
    )


def _summarize_slot(slot: int) -> _ReplaySlotSummary:
    loaded = _load_replay_slot(slot)
    if loaded is not None:
        return loaded.summary
    return _ReplaySlotSummary(
        slots_loaded=0,
        total_actions=0,
        decoded_actions=0,
        unsupported_program_ids=(),
    )


def _load_replay_slot(slot: int) -> _LoadedReplaySlot | None:
    cached = _cached_loaded_slot(slot)
    if cached is not None:
        return cached
    snapshot = _load_slot(slot)
    if snapshot is None:
        return None
    summary = _summarize_loaded_slot(snapshot)
    loaded = _LoadedReplaySlot(snapshot=snapshot, summary=summary)
    _store_loaded_slot(slot, loaded)
    return loaded


def _summarize_slot_range(start: int, end: int) -> _ReplayRangeSummary:
    _, summary = _load_slot_range(start, end)
    return summary


def _load_slot_range(
    start: int,
    end: int,
) -> tuple[list[_LoadedReplaySlot], _ReplayRangeSummary]:
    loaded_slots: list[_LoadedReplaySlot] = []
    slots_loaded = 0
    total_actions = 0
    decoded_actions = 0
    unsupported: dict[str, None] = {}
    for slot in range(start, end + 1):
        loaded = _load_replay_slot(slot)
        if loaded is None:
            continue
        loaded_slots.append(loaded)
        summary = loaded.summary
        slots_loaded += summary.slots_loaded
        total_actions += summary.total_actions
        decoded_actions += summary.decoded_actions
        for pid in summary.unsupported_program_ids:
            unsupported.setdefault(pid, None)
    return (
        loaded_slots,
        _ReplayRangeSummary(
            slots_loaded=slots_loaded,
            total_actions=total_actions,
            decoded_actions=decoded_actions,
            unsupported_program_ids=tuple(unsupported.keys()),
        ),
    )


def _execute_replay(
    loaded_slots: list[_LoadedReplaySlot],
    counterfactuals: list[Any],
) -> tuple[RunSnapshot, dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if not loaded_slots:
        predicted = RunSnapshot()
        return predicted, predicted.to_dict(), [], None

    world = _build_replay_world(loaded_slots, counterfactuals)
    replay_state = _ReplayEngineState(markets=world.markets)
    execution = ReplayExecution(
        slot_stream=iter(loaded.snapshot for loaded in loaded_slots)
    )
    execution.bind_replay_state(replay_state)
    for cf in counterfactuals:
        if isinstance(cf, Counterfactual):
            execution.add_counterfactual(cf)
    clock = SolanaSlotClock(skip_rate=0.0)
    engine = SimulationEngine(
        world,
        _build_replay_agents(loaded_slots, counterfactuals),
        SimulationConfig(
            num_rounds=max(loaded.snapshot.slot for loaded in loaded_slots),
            clock=clock,
            execution_model=execution,
        ),
    )

    per_slot_snapshots: list[RunSnapshot] = []
    action_payloads: list[dict[str, Any]] = []
    round_snapshots: list[dict[str, Any]] = []
    for loaded in loaded_slots:
        _prepare_engine_for_replay_slot(
            engine=engine,
            clock=clock,
            slot=loaded.snapshot.slot,
        )
        engine_snapshot = engine.step()
        outcome = execution._last_replay_outcome
        historical = loaded.snapshot
        actions = list(getattr(execution, "_last_replay_submitted_actions", ()))
        diagnostics = list(getattr(execution, "_last_replay_diagnostics", ()))
        predicted = _run_snapshot_from_engine_slot(
            engine_snapshot=engine_snapshot,
            outcome=outcome,
            diagnostics=diagnostics,
            slot=historical.slot,
        )
        per_slot_snapshots.append(predicted)
        action_payloads.extend(
            _action_payload(action, slot=historical.slot, index=index)
            for index, action in enumerate(actions)
        )
        round_snapshot = predicted.to_dict()
        round_snapshot.update(
            {
                "round": historical.slot,
                "slot": historical.slot,
                "timestamp": historical.block_time or 0,
                "decoded_transaction_share": loaded.summary.slots_loaded
                and (
                    loaded.summary.decoded_actions / loaded.summary.total_actions
                    if loaded.summary.total_actions
                    else 0.0
                ),
                "unsupported_program_ids": list(loaded.summary.unsupported_program_ids),
            }
        )
        round_snapshots.append(round_snapshot)

    aggregate = _combine_run_snapshots(per_slot_snapshots)
    predicted_payload = aggregate.to_dict()
    predicted_payload["actions"] = action_payloads
    replay_diff = _replay_diff_payload(
        predicted=aggregate,
        actual=_aggregate_actual_snapshot(loaded_slots),
    )
    return aggregate, predicted_payload, round_snapshots, replay_diff


def _build_replay_world(
    loaded_slots: list[_LoadedReplaySlot],
    counterfactuals: list[Any],
) -> World:
    world = World()
    world.add_market(
        REPLAY_ACCOUNTING_MARKET,
        _ReplayAccountingMarket(REPLAY_ACCOUNTING_MARKET),
    )
    for loaded in loaded_slots:
        for action in materialize_slot(loaded.snapshot):
            market_name = replay_market_name_for_action(action)
            if not market_name or market_name == REPLAY_ACCOUNTING_MARKET:
                continue
            if market_name not in world.markets:
                world.add_market(market_name, _ReplayProtocolMarket(market_name))
    for cf in counterfactuals:
        target_pool = getattr(cf, "target_pool", None)
        if target_pool and str(target_pool) not in world.markets:
            name = str(target_pool)
            world.add_market(name, _ReplayProtocolMarket(name))
    return world


def _build_replay_agents(
    loaded_slots: list[_LoadedReplaySlot],
    counterfactuals: list[Any],
) -> list[Agent]:
    agent_ids: dict[str, None] = {}
    for loaded in loaded_slots:
        for action in materialize_slot(loaded.snapshot):
            agent_ids.setdefault(str(action.agent_id), None)
    for cf in counterfactuals:
        agent_id = getattr(cf, "agent_id", None)
        if agent_id is not None:
            agent_ids.setdefault(str(agent_id), None)
        agent = getattr(cf, "agent", None)
        injected_agent_id = getattr(agent, "agent_id", None)
        if injected_agent_id is not None:
            agent_ids.setdefault(str(injected_agent_id), None)
    return [_ReplayAgent(agent_id) for agent_id in agent_ids]


def _prepare_engine_for_replay_slot(
    *,
    engine: SimulationEngine,
    clock: SolanaSlotClock,
    slot: int,
) -> None:
    engine._current_round = slot - 1  # noqa: SLF001 - replay maps rounds to slots.
    clock.current_slot = slot - 1


def _run_snapshot_from_engine_slot(
    *,
    engine_snapshot: Any,
    outcome: Any,
    diagnostics: list[Action],
    slot: int,
) -> RunSnapshot:
    executed = list(getattr(outcome, "executed", ()) or ())
    predicted = _run_snapshot_from_executed_actions(
        executed=executed,
        diagnostics=diagnostics,
        slot=slot,
    )
    _apply_engine_market_state_metrics(
        predicted,
        all_market_states=getattr(engine_snapshot, "all_market_states", None),
        slot=slot,
    )
    return predicted


def _run_snapshot_from_executed_actions(
    *,
    executed: list[ExecutedAction],
    diagnostics: list[Action],
    slot: int,
) -> RunSnapshot:
    snapshot = RunSnapshot()
    landed_any = False
    for diagnostic in diagnostics:
        _record_replay_action_common(snapshot, diagnostic, slot, landed=False)
        snapshot.unsupported_instruction_coverage += 1

    for item in executed:
        action = unwrap_replay_action(item.action)
        status = action_decode_status(action)
        landed = item.succeeded and status is ActionDecodeStatus.DECODED
        if landed:
            landed_any = True
        elif status is not ActionDecodeStatus.DECODED:
            snapshot.unsupported_instruction_coverage += 1
        _record_replay_action_common(snapshot, action, slot, landed=landed)
        _record_replay_action_type_metrics(snapshot, action, slot, landed=landed)

    if landed_any:
        snapshot.skip_rate_samples.append(
            (False, int(snapshot.tips_paid + snapshot.total_volume))
        )
    return snapshot


def _record_replay_action_common(
    snapshot: RunSnapshot,
    action: Action,
    slot: int,
    *,
    landed: bool,
) -> None:
    snapshot.submission_path_samples.append((action.submission_path, landed))
    for account in getattr(action, "write_locks", frozenset()):
        snapshot.write_lock_claims.append((str(account), slot))


def _record_replay_action_type_metrics(
    snapshot: RunSnapshot,
    action: Action,
    slot: int,
    *,
    landed: bool,
) -> None:
    if isinstance(action, TokenTransferAction):
        for account in (action.source, action.destination):
            if account:
                snapshot.write_lock_claims.append((account, slot))

    if isinstance(action, MaterializedSwapAction):
        amount_in = float(action.amount_in or 0)
        if landed:
            snapshot.total_volume += amount_in
            snapshot.decoded_swap_count += 1
        if action.pool_id:
            snapshot.write_lock_claims.append((action.pool_id, slot))
        for account in action.pool_reserve_accounts:
            snapshot.write_lock_claims.append((account, slot))
        if landed and action.pool_id and action.amount_out is not None and amount_in:
            snapshot.pool_prices[action.pool_id] = float(action.amount_out) / amount_in

    if isinstance(action, LiquidateAction) and landed:
        snapshot.liquidations_triggered += 1
        snapshot.decoded_liquidation_count += 1

    if isinstance(action, LPAction) and landed:
        amount = float(getattr(action, "amount", 0) or 0)
        agent_id = str(action.agent_id)
        snapshot.lp_balances[agent_id] = (
            snapshot.lp_balances.get(agent_id, 0.0) + amount
        )
        snapshot.decoded_lp_action_count += 1

    tip_lamports = getattr(action, "tip_lamports", None)
    if isinstance(tip_lamports, int):
        _record_replay_tip_metrics(
            snapshot,
            action,
            slot,
            tip_lamports=tip_lamports,
            landed=landed and tip_lamports >= MIN_BUNDLE_TIP_LAMPORTS,
        )


def _record_replay_tip_metrics(
    snapshot: RunSnapshot,
    action: Action,
    slot: int,
    *,
    tip_lamports: int,
    landed: bool,
) -> None:
    if landed:
        snapshot.tips_paid += tip_lamports
    bundle_id = getattr(action, "bundle_id", None)
    if not bundle_id:
        return
    snapshot.bundle_outcomes.append(
        BundleOutcome(
            slot=slot,
            bundle_index=len(snapshot.bundle_outcomes),
            status="landed" if landed else "dropped",
            tip_lamports=tip_lamports,
            validator_revenue_lamports=tip_lamports if landed else 0,
            stake_pool_revenue_lamports=0,
            num_txs=1,
            total_cu=int(action.compute_unit_limit or 0),
            drop_reason=None if landed else "bundle_tip_below_minimum",
        )
    )
    if not landed:
        return
    submitted_slot = slot
    metadata = getattr(action, "materialized_metadata", None)
    metadata_slot = getattr(metadata, "slot", None)
    if isinstance(metadata_slot, int):
        submitted_slot = metadata_slot
    snapshot.slot_inclusion_samples.append((submitted_slot, slot))
    ev = getattr(action, "extracted_value_lamports", None)
    if isinstance(ev, int) and ev > 0:
        snapshot.tip_efficiency_samples.append((tip_lamports, ev))
        snapshot.breakeven_samples.append((tip_lamports, ev))


def _apply_engine_market_state_metrics(
    snapshot: RunSnapshot,
    *,
    all_market_states: dict[str, MarketSnapshot] | None,
    slot: int,
) -> None:
    if not all_market_states:
        return
    market_volume = 0.0
    saw_market_volume = False
    for market_name, market_state in all_market_states.items():
        if market_name == REPLAY_ACCOUNTING_MARKET:
            continue
        last_slot = getattr(market_state, "last_slot", None)
        if last_slot != slot:
            continue
        slot_volume = getattr(market_state, "slot_volume", None)
        if slot_volume is not None:
            market_volume += float(slot_volume)
            saw_market_volume = True
        price = getattr(market_state, "price", None)
        if price is not None:
            snapshot.pool_prices[market_name] = float(price)
    if saw_market_volume:
        snapshot.total_volume = market_volume


def _combine_run_snapshots(snapshots: list[RunSnapshot]) -> RunSnapshot:
    combined = RunSnapshot()
    for snapshot in snapshots:
        combined.pool_prices.update(snapshot.pool_prices)
        for agent_id, balance in snapshot.lp_balances.items():
            combined.lp_balances[agent_id] = (
                combined.lp_balances.get(agent_id, 0.0) + balance
            )
        combined.total_volume += snapshot.total_volume
        combined.liquidations_triggered += snapshot.liquidations_triggered
        combined.tips_paid += snapshot.tips_paid
        combined.unsupported_instruction_coverage += (
            snapshot.unsupported_instruction_coverage
        )
        combined.bundle_outcomes.extend(snapshot.bundle_outcomes)
        combined.tip_efficiency_samples.extend(snapshot.tip_efficiency_samples)
        combined.slot_inclusion_samples.extend(snapshot.slot_inclusion_samples)
        combined.breakeven_samples.extend(snapshot.breakeven_samples)
        combined.skip_rate_samples.extend(snapshot.skip_rate_samples)
        combined.write_lock_claims.extend(snapshot.write_lock_claims)
        combined.submission_path_samples.extend(snapshot.submission_path_samples)
        combined.decoded_swap_count += snapshot.decoded_swap_count
        combined.decoded_liquidation_count += snapshot.decoded_liquidation_count
        combined.decoded_lp_action_count += snapshot.decoded_lp_action_count
    return combined


def _aggregate_actual_snapshot(loaded_slots: list[_LoadedReplaySlot]) -> RunSnapshot:
    return _combine_run_snapshots(
        [extract_actual_metrics(loaded.snapshot) for loaded in loaded_slots]
    )


def _replay_diff_payload(
    *,
    predicted: RunSnapshot,
    actual: SlotSnapshot | RunSnapshot,
) -> dict[str, Any]:
    diff = ReplayDiff(predicted=predicted, actual=actual)
    thresholds = load_thresholds()
    return {
        "per_metric_error": {
            metric: _error_band_payload(metric, band, thresholds)
            for metric, band in diff.per_metric_error().items()
        },
        "unsupported_instruction_coverage": diff.unsupported_instruction_coverage,
    }


def _error_band_payload(
    metric: str,
    band: Any,
    thresholds: dict[str, Threshold],
) -> dict[str, Any]:
    threshold = _threshold_for_metric(metric, thresholds)
    threshold_value = _threshold_value(threshold)
    return {
        "metric": band.metric,
        "predicted": band.predicted,
        "actual": band.actual,
        "abs_error": band.abs_error,
        "rel_error": band.rel_error,
        "absolute_error": band.abs_error,
        "relative_error": band.rel_error,
        "threshold": threshold_value,
        "threshold_kind": _threshold_kind(threshold),
        "supported": band.supported,
    }


def _threshold_for_metric(
    metric: str,
    thresholds: dict[str, Threshold],
) -> Threshold | None:
    if metric in thresholds:
        return thresholds[metric]
    return thresholds.get(metric.split(":", 1)[0])


def _threshold_value(threshold: Threshold | None) -> float | None:
    if threshold is None:
        return None
    if threshold.threshold_relative is not None:
        return threshold.threshold_relative
    return threshold.threshold_absolute


def _threshold_kind(threshold: Threshold | None) -> str | None:
    if threshold is None:
        return None
    if threshold.threshold_relative is not None:
        return "relative"
    return "absolute"


def _action_signature(action: Action) -> str | None:
    signature = getattr(action, "signature", None)
    if isinstance(signature, str):
        return signature
    metadata = getattr(action, "materialized_metadata", None)
    metadata_signature = getattr(metadata, "signature", None)
    return metadata_signature if isinstance(metadata_signature, str) else None


def _action_payload(action: Action, *, slot: int, index: int) -> dict[str, Any]:
    action = unwrap_replay_action(action)
    signature = _action_signature(action)
    payload: dict[str, Any] = {
        "slot": slot,
        "index": index,
        "type": type(action).__name__,
        "agent_id": str(action.agent_id),
        "signature": signature,
        "decode_status": action_decode_status(action).value,
        "compute_unit_limit": action.compute_unit_limit,
        "submission_path": action.submission_path,
    }
    bundle_id = getattr(action, "bundle_id", None)
    if bundle_id is not None:
        payload["bundle_id"] = bundle_id
    tip_lamports = getattr(action, "tip_lamports", None)
    if tip_lamports is not None:
        payload["tip_lamports"] = tip_lamports
    if isinstance(action, MaterializedSwapAction):
        payload.update(
            {
                "pool_id": action.pool_id,
                "token_in": action.token_in,
                "token_out": action.token_out,
                "amount_in": action.amount_in,
                "amount_out": action.amount_out,
                "pool_reserve_accounts": list(action.pool_reserve_accounts),
                "active_bin_id": action.active_bin_id,
                "bin_array_bitmap_extension": action.bin_array_bitmap_extension,
            }
        )
    return payload


@router.post(
    "",
    response_model=ReplayResponse,
    status_code=status.HTTP_200_OK,
    summary="Replay a slot range with optional counterfactual injection",
)
def post_replay(
    body: ReplayRequest,
    user: User = Depends(current_user),
) -> ReplayResponse:
    start, end = body.slot_range
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"slot_range end ({end}) must be >= start ({start})",
        )

    counterfactuals = [_decode_counterfactual(spec) for spec in body.counterfactuals]

    loaded_slots, summary = _load_slot_range(start, end)
    decoded_share = summary.decoded_transaction_share
    # FIX-016: undecoded txs are ignored proactively rather than blocking
    # the run. Eligibility requires (a) at least one decoded action so
    # the diff has real signal, and (b) every slot has a non-synthetic
    # corpus manifest so the run is comparing against real evidence. We
    # no longer demand every tx in the block be decoded — as decoders
    # ship, more txs flip from opaque to decoded automatically.
    eligible = (
        summary.decoded_actions > 0
        and _all_slots_corpus_calibrated(start, end)
    )

    _, predicted_payload, round_snapshots, replay_diff = _execute_replay(
        loaded_slots,
        counterfactuals,
    )

    # PRD line 338 / FIX-005 (refined by FIX-016): a replay can claim
    # mainnet accuracy when (a) at least one action was decoded so the
    # diff is non-vacuous, (b) every slot in the range has a non-synthetic
    # corpus manifest, and (c) every comparable per-metric error band
    # meets its configured threshold. Decoded coverage is reported as a
    # diagnostic (``decoded_transaction_share``) but is not a hard gate:
    # undecoded txs land as OpaqueAction and contribute nothing to either
    # side of the diff, so they are safe to ignore.
    replay_kind = "development_or_partial_replay"
    mainnet_accuracy_claim = False
    if eligible and _diff_within_thresholds(replay_diff):
        replay_kind = "mainnet_calibrated_replay"
        mainnet_accuracy_claim = True

    run_id = state.new_id()
    unsupported_ids = list(summary.unsupported_program_ids)
    record = persist_replay_run(
        run_id,
        slot_range=(start, end),
        counterfactuals=counterfactuals,
        predicted=predicted_payload,
        replay_diff=replay_diff,
        round_snapshots=round_snapshots,
        seed=body.seed,
        decoded_transaction_share=decoded_share,
        unsupported_program_ids=unsupported_ids,
        replay_kind=replay_kind,
        mainnet_accuracy_claim=mainnet_accuracy_claim,
        owner_id=owner_for_create(user),
    )
    cf_summary = list(record.get("summary", {}).get("counterfactuals", []))

    return ReplayResponse(
        run_id=run_id,
        slot_range=(start, end),
        slots_loaded=summary.slots_loaded,
        counterfactuals=cf_summary,
        decoded_transaction_share=decoded_share,
        unsupported_program_ids=unsupported_ids,
        eligible_for_calibration=eligible,
        replay_kind=replay_kind,
        mainnet_accuracy_claim=mainnet_accuracy_claim,
    )


@router.get(
    "/targets/{slot}",
    response_model=ReplayTargetsResponse,
    summary="List counterfactual targets (bundle IDs, pool IDs) present in a slot",
)
def get_replay_targets(slot: int) -> ReplayTargetsResponse:
    """Return real bundle IDs and pool IDs materialized from a slot.

    Tip and FeeReplace counterfactuals match by bundle ID and pool address
    respectively. Without this endpoint the UI has no way to surface valid
    targets, so users type placeholder values that match nothing and the
    counterfactual silently no-ops.
    """
    loaded = _load_replay_slot(slot)
    if loaded is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"slot {slot} is not available",
        )

    bundles: dict[str, dict[str, int]] = {}
    pools: dict[str, int] = {}
    for action in materialize_slot(loaded.snapshot):
        bundle_id = getattr(action, "bundle_id", None)
        if isinstance(bundle_id, str) and bundle_id:
            entry = bundles.setdefault(
                bundle_id, {"tip_lamports": 0, "num_actions": 0}
            )
            entry["num_actions"] += 1
            tip = getattr(action, "tip_lamports", None)
            if isinstance(tip, int) and tip > entry["tip_lamports"]:
                entry["tip_lamports"] = tip
        if isinstance(action, MaterializedSwapAction):
            pool_id = action.pool_id
            if isinstance(pool_id, str) and pool_id:
                pools[pool_id] = pools.get(pool_id, 0) + 1

    bundle_targets = [
        ReplayBundleTarget(
            bundle_id=bid,
            tip_lamports=meta["tip_lamports"],
            num_actions=meta["num_actions"],
        )
        for bid, meta in bundles.items()
    ]
    bundle_targets.sort(key=lambda b: (-b.tip_lamports, -b.num_actions, b.bundle_id))
    pool_targets = [
        ReplayPoolTarget(pool_id=pid, decoded_swaps=count)
        for pid, count in pools.items()
    ]
    pool_targets.sort(key=lambda p: (-p.decoded_swaps, p.pool_id))
    return ReplayTargetsResponse(
        slot=slot, bundles=bundle_targets, pools=pool_targets
    )
