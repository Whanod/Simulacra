"""Agent ABC and supporting types.

Agent — base class for all simulation agents.
DecisionContext — market state + metadata passed to agents each round.
InformationFilter — controls what each agent sees.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from defi_sim.core.market import deserialize_callable_ref, serialize_callable_ref
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentState,
    MarketSnapshot,
    RoundSnapshot,
    TokenId,
)


@dataclass
class DecisionContext:
    market_state: MarketSnapshot | None = None
    current_round: int = 0
    total_rounds: int = 200
    timestamp: int | float = 0
    epoch: int = 0
    agent_state: AgentState = field(default_factory=lambda: AgentState(agent_id=""))

    # Typed optional fields for common agent needs.
    belief: dict[TokenId, int] | None = None
    feed_prices: dict[TokenId, int] | None = None
    visible_agents: list[AgentId] | None = None
    pending_actions: list[Action] | None = None
    parameters: Any = None  # ParameterStore | None
    priority_fee_market: Any = None  # PriorityFeeMarket | None — kept Any to avoid engine→core cycle

    # PRD US-001 line 108: agents see the current slot and the slot's leader
    # pubkey so leader-targeted submissions and slot-aware logic can read them
    # off DecisionContext (parallel to the OrderingContext fields used by
    # markets). Both are None when the engine is not running a Solana clock.
    current_slot: int | None = None
    current_leader: str | None = None

    # PRD US-004 line 368: when an action submitted by this agent in the
    # previous slot was dropped, the reason lands here keyed by drop reason.
    # Default behaviour is observe-only — agents opt in to re-submission via
    # ``Agent.should_resubmit_on_drop``.
    last_drop_reasons: dict[str, list[Action]] | None = None

    # PRD US-013: searcher agents submit Jito bundles via this side-channel
    # rather than as Actions, since Bundles aren't the Action type. Engine
    # supplies a callable that forwards to ``SolanaLikeExecution.submit_bundle``.
    submit_bundle: Any = None  # Callable[[Bundle], None] | None

    # PRD US-013: per-action lock resolution callable. JitoSearcher needs the
    # pool account a victim swap would write so it can quote the fee market at
    # that account. Engine wires this to ``SimulationEngine._resolve_action_locks``;
    # standalone fixtures leave it ``None`` and searchers fall back gracefully.
    resolve_locks: Any = None  # Callable[[Action], LockedAction | None] | None

    extra: dict[str, Any] = field(default_factory=dict)


class Agent(ABC):
    agent_id: AgentId
    state: AgentState

    @abstractmethod
    def decide(self, ctx: DecisionContext) -> list[Action]: ...

    def on_round_end(self, round: int, snapshot: RoundSnapshot) -> None:
        """Called after each round completes. Override to update internal state."""
        pass

    def on_event(self, event: Any) -> None:
        """Called when an event the agent is subscribed to fires."""
        pass

    def should_resubmit_on_drop(self, reason: str) -> bool:
        """PRD US-004 line 368: agent re-submission semantics.

        When an action is dropped (e.g., ``submission_path_drop``), the
        engine surfaces the reason to the originating agent. Re-submission
        is opt-in per agent — default is ``False`` so noise/baseline agents
        do not generate retry storms. Agents like ``JitoSearcher`` can
        override to re-submit (potentially with a higher tip) on specific
        drop reasons.
        """
        return False


# ---------------------------------------------------------------------------
# InformationFilter
# ---------------------------------------------------------------------------


class InformationFilter(ABC):
    """Controls what information each agent receives in its DecisionContext."""

    @abstractmethod
    def filter_market_state(self, agent: Agent, state: MarketSnapshot) -> MarketSnapshot: ...

    @abstractmethod
    def filter_feed_prices(self, agent: Agent, prices: dict[TokenId, int]) -> dict[TokenId, int] | None: ...

    def filter_all_market_states(
        self,
        agent: Agent,
        states: dict[str, MarketSnapshot],
    ) -> dict[str, MarketSnapshot]:
        """Default world-mode behavior: filter each market snapshot independently."""
        return {
            name: self.filter_market_state(agent, state)
            for name, state in states.items()
        }


class FullTransparency(InformationFilter):
    """Every agent sees full state. Default. No mempool visibility."""

    def filter_market_state(self, agent: Agent, state: MarketSnapshot) -> MarketSnapshot:
        return state

    def filter_feed_prices(self, agent: Agent, prices: dict[TokenId, int]) -> dict[TokenId, int] | None:
        return prices


class DelayedInformation(InformationFilter):
    """Some agents see state delayed by N rounds."""

    def __init__(self, delays: dict[str, int]):
        """delays: agent role name -> rounds of delay. Unspecified roles get 0."""
        self._delays = delays
        self._history: list[MarketSnapshot | dict[str, MarketSnapshot]] = []
        self._price_history: list[dict[TokenId, int]] = []

    def record(
        self,
        state: MarketSnapshot | dict[str, MarketSnapshot],
        prices: dict[TokenId, int] | None = None,
    ) -> None:
        self._history.append(copy.deepcopy(state))
        self._price_history.append(dict(prices or {}))

    def filter_market_state(self, agent: Agent, state: MarketSnapshot) -> MarketSnapshot:
        delay = self._delays.get(agent.state.role.name, 0)
        if delay > 0 and len(self._history) >= delay:
            delayed = self._history[-delay]
            if isinstance(delayed, dict):
                return state
            return copy.deepcopy(delayed)
        return state

    def filter_all_market_states(
        self,
        agent: Agent,
        states: dict[str, MarketSnapshot],
    ) -> dict[str, MarketSnapshot]:
        delay = self._delays.get(agent.state.role.name, 0)
        if delay > 0 and len(self._history) >= delay:
            delayed = self._history[-delay]
            if isinstance(delayed, dict):
                return copy.deepcopy(delayed)
        return super().filter_all_market_states(agent, states)

    def filter_feed_prices(self, agent: Agent, prices: dict[TokenId, int]) -> dict[TokenId, int] | None:
        delay = self._delays.get(agent.state.role.name, 0)
        if delay > 0 and len(self._price_history) >= delay:
            return self._price_history[-delay]
        return prices


def _serialize_snapshot_view(
    view: MarketSnapshot | dict[str, MarketSnapshot],
) -> dict[str, Any]:
    if isinstance(view, dict):
        return {
            "kind": "world",
            "markets": {
                name: _serialize_snapshot_view(snapshot)
                for name, snapshot in view.items()
            },
        }

    if not is_dataclass(view):
        raise TypeError(f"cannot snapshot information filter state for {type(view)!r}")

    cls_ref = serialize_callable_ref(view.__class__)
    if cls_ref is None:
        raise TypeError(f"market snapshot class {view.__class__.__name__} is not serializable")

    return {
        "kind": "single",
        "class_ref": cls_ref,
        "data": asdict(view),
    }


def _deserialize_snapshot_view(data: dict[str, Any]) -> MarketSnapshot | dict[str, MarketSnapshot]:
    kind = data.get("kind")
    if kind == "world":
        markets = data.get("markets", {})
        return {
            name: _deserialize_snapshot_view(snapshot)
            for name, snapshot in markets.items()
        }
    if kind != "single":
        raise ValueError(f"unknown information-filter snapshot kind: {kind}")

    snapshot_cls = deserialize_callable_ref(data["class_ref"])
    return snapshot_cls(**data["data"])


def serialize_information_filter(filter_obj: InformationFilter) -> dict[str, Any]:
    if isinstance(filter_obj, FullTransparency):
        return {"type": "full_transparency"}

    if isinstance(filter_obj, DelayedInformation):
        return {
            "type": "delayed_information",
            "delays": dict(filter_obj._delays),
            "history": [
                _serialize_snapshot_view(view)
                for view in filter_obj._history
            ],
            "price_history": [
                dict(price_map)
                for price_map in filter_obj._price_history
            ],
        }

    raise TypeError(
        f"information filter {type(filter_obj).__name__} is not snapshot-serializable"
    )


def deserialize_information_filter(data: dict[str, Any]) -> InformationFilter:
    filter_type = data.get("type")
    if filter_type == "full_transparency":
        return FullTransparency()
    if filter_type == "delayed_information":
        info_filter = DelayedInformation(data.get("delays", {}))
        info_filter._history = [
            _deserialize_snapshot_view(view)
            for view in data.get("history", [])
        ]
        info_filter._price_history = [
            dict(price_map)
            for price_map in data.get("price_history", [])
        ]
        return info_filter

    raise ValueError(f"unknown information filter type: {filter_type}")
