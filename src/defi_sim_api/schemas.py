"""Pydantic models for the defi-sim web API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from defi_sim.core.types import (
    Action,
    BundleAction,
    LPAction,
    LPActionType,
    OrderAction,
    OrderSide,
    Side,
    SingleAssetAction,
    SwapAction,
)


def _to_camel(value: str) -> str:
    """Lower-camelCase alias generator used by the registry contract
    models so the JSON surface matches the frontend TS types from
    ``frontend/src/lib/types/contract.ts`` without a boundary
    conversion layer."""
    parts = value.split("_")
    if not parts:
        return value
    return parts[0] + "".join(p.title() for p in parts[1:] if p)


# ---------------------------------------------------------------------------
# Spec schemas (mirror engine/specs.py dataclasses as JSON-friendly models)
# ---------------------------------------------------------------------------

class TokenSpecSchema(BaseModel):
    id: str
    symbol: str
    decimals: int = 18


class PairSpecSchema(BaseModel):
    base: TokenSpecSchema
    quote: TokenSpecSchema


class ClockSpecSchema(BaseModel):
    type: str = "block"
    params: dict[str, Any] = Field(default_factory=dict)


class OrderingSpecSchema(BaseModel):
    type: str = "fifo"
    params: dict[str, Any] = Field(default_factory=dict)


class GasSpecSchema(BaseModel):
    type: str = "zero"
    params: dict[str, Any] = Field(default_factory=dict)


class InformationFilterSpecSchema(BaseModel):
    type: str = "full_transparency"
    params: dict[str, Any] = Field(default_factory=dict)


class FeeModelSpecSchema(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class FeedSpecSchema(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    feeds: dict[str, "FeedSpecSchema"] = Field(default_factory=dict)


class ExecutionSpecSchema(BaseModel):
    type: str = "direct"
    params: dict[str, Any] = Field(default_factory=dict)
    ordering: OrderingSpecSchema | None = None
    gas_model: GasSpecSchema | None = None


class MarketSpecSchema(BaseModel):
    type: str
    tokens: list[TokenSpecSchema] = Field(default_factory=list)
    pairs: list[PairSpecSchema] = Field(default_factory=list)
    fee_model: FeeModelSpecSchema | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class WorldSpecSchema(BaseModel):
    type: str = "world"
    markets: dict[str, MarketSpecSchema] = Field(default_factory=dict)


class AgentSpecSchema(BaseModel):
    type: str
    agent_id: str | int
    params: dict[str, Any] = Field(default_factory=dict)
    initial_balances: dict[str, Any] = Field(default_factory=dict)
    initial_cumulative_volume: int | float = 0
    initial_realized_pnl: int | float = 0


class RunSpecSchema(BaseModel):
    """Top-level simulation specification — accepts both single-market and world specs."""

    market: dict[str, Any]
    agents: list[AgentSpecSchema] = Field(default_factory=list)
    num_rounds: int = 200
    snapshot_interval: int = 10
    seed: int = 42
    retain_snapshots: bool = True
    numeric_mode: str = "fixed"
    clock: ClockSpecSchema | None = None
    ordering: OrderingSpecSchema | None = None
    gas_model: GasSpecSchema | None = None
    execution: ExecutionSpecSchema | None = None
    information_filter: InformationFilterSpecSchema | None = None
    default_fee_model: FeeModelSpecSchema | None = None
    feeds: list[FeedSpecSchema] = Field(default_factory=list)
    alts: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Opt-in trigger for ``apply_lighthouse_sizing`` (see
    # ``defi_sim_api.backend.lighthouse_sizing``). When True on a
    # ``market.type == "whirlpool"`` spec, agent trade caps and balances
    # are rewritten as fractions of the captured token-B vault depth so
    # flow tracks the ``initial_liquidity`` slider. Default False so
    # non-lighthouse callers see no behaviour change. Must be declared
    # on the schema or Pydantic drops it on ``model_dump`` and the
    # rescaling pass never fires.
    lighthouse_sizing: bool = False


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class SimulationResultResponse(BaseModel):
    """Wrapper around the JSON-serialized SimulationResult."""

    run_id: str | None = None
    result: dict[str, Any]


class SimulationStatusResponse(BaseModel):
    simulation_id: str
    run_id: str | None = None
    current_round: int
    is_complete: bool
    cancelled: bool = False


class EngineCreatedResponse(BaseModel):
    simulation_id: str
    run_id: str | None = None
    current_round: int
    is_complete: bool


class StepResponse(BaseModel):
    simulation_id: str
    run_id: str | None = None
    round: int
    snapshot: dict[str, Any]
    is_complete: bool


class CancelResponse(BaseModel):
    simulation_id: str
    cancelled: bool
    reason: str | None = None


class SnapshotResponse(BaseModel):
    simulation_id: str
    snapshot_bytes_hex: str


class RestoreRequest(BaseModel):
    snapshot_bytes_hex: str


class RestoreResponse(BaseModel):
    simulation_id: str
    restored: bool
    current_round: int


# ---------------------------------------------------------------------------
# Registry / catalog responses
# ---------------------------------------------------------------------------

_REGISTRY_CONTRACT_CONFIG = ConfigDict(
    alias_generator=_to_camel,
    populate_by_name=True,
)


class RegistryEntityDefinition(BaseModel):
    """Enriched per-entity registry payload (BE-002).

    Mirrors the frontend ``RegistryEntityDefinition`` contract defined
    in ``frontend/src/lib/types/contract.ts``. Emitted as camelCase so
    the frontend renderer can consume the response directly without a
    boundary conversion layer.
    """

    model_config = _REGISTRY_CONTRACT_CONFIG

    category: str
    type: str
    label: str
    description: str = ""
    badges: list[dict[str, str]] | None = None
    color_hint: str | None = None
    builder_supported: bool = True
    # Pydantic forbids `schema` as a field name on BaseModel, so use a
    # trailing underscore internally and alias to the JSON key.
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    ui_schema: dict[str, Any] | None = None
    defaults: dict[str, Any] | None = None
    examples: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


class RegistryCategoryDefinition(BaseModel):
    """Enriched category payload (BE-002)."""

    model_config = _REGISTRY_CONTRACT_CONFIG

    key: str
    label: str
    description: str = ""
    order: int | None = None
    entities: list[RegistryEntityDefinition]


class RegistryContractResponse(BaseModel):
    """Top-level response for ``GET /registry`` (BE-002).

    ``contractVersion`` (emitted as camelCase) lets frontend consumers
    degrade gracefully when the backend ships a newer shape. The
    current version is ``v2``; the pre-v2 shape was a flat
    ``dict[str, list[str]]`` and was removed in BE-006.
    """

    model_config = _REGISTRY_CONTRACT_CONFIG

    contract_version: str
    categories: list[RegistryCategoryDefinition]


class SpecValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class MetricsComputeRequest(BaseModel):
    result: dict[str, Any]
    metrics: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Map of metric_name -> {type, params}. If empty, returns empty.",
    )


class MetricsResponse(BaseModel):
    metrics: dict[str, float]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

class LeaderboardRequest(BaseModel):
    data: list[dict[str, Any]]
    group_col: str
    score_col: str
    title: str = "Leaderboard"


class BoxPlotRequest(BaseModel):
    data: list[dict[str, Any]]
    group_col: str
    metric_col: str
    title: str = "Metric Distribution"


class TimeSeriesRequest(BaseModel):
    series: list[float]
    ci_low: list[float] | None = None
    ci_high: list[float] | None = None
    title: str = "Time Series"
    y_label: str = "Value"


class HeatmapRequest(BaseModel):
    data: list[dict[str, Any]]
    x_col: str
    y_col: str
    value_col: str
    title: str = "Heatmap"


class ChartResponse(BaseModel):
    chart: dict[str, Any]


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------

class SweepRankRequest(BaseModel):
    data: list[dict[str, Any]]
    metric_columns: list[str]
    weights: dict[str, float] | None = None
    lower_is_better: dict[str, bool] | None = None
    group_col: str | None = None
    top_k: int = 3


class SweepSensitivityRequest(BaseModel):
    data: list[dict[str, Any]]
    param: str
    metric: str


class SweepAnalysisResponse(BaseModel):
    data: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationCheckRequest(BaseModel):
    """Run validation checks against a simulation result snapshot."""

    spec: dict[str, Any]
    checks: list[str] = Field(
        default_factory=lambda: ["conservation", "solvency", "reserves"],
    )


class ValidationCheckResponse(BaseModel):
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Engine introspection
# ---------------------------------------------------------------------------

class EventResponse(BaseModel):
    events: list[dict[str, Any]]


class MarketStateResponse(BaseModel):
    simulation_id: str
    market_name: str | None = None
    state: dict[str, Any]


class AllMarketStatesResponse(BaseModel):
    simulation_id: str
    states: dict[str, dict[str, Any]]


class AgentStateResponse(BaseModel):
    agent_id: str | int
    balances: dict[str, Any]
    cumulative_volume: int | float
    cumulative_volume_quote: int | float = 0
    realized_pnl: int | float


class AllAgentStatesResponse(BaseModel):
    simulation_id: str
    agents: dict[str | int, AgentStateResponse]


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

class ParameterSetRequest(BaseModel):
    key: str
    value: Any


class ScheduledChangeRequest(BaseModel):
    key: str
    value: Any
    execute_at_round: int
    proposed_by: str | int | None = None
    proposal_id: str | None = None


class ParameterStoreResponse(BaseModel):
    params: dict[str, Any]
    pending: list[dict[str, Any]] = Field(default_factory=list)
    history: list[list[Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Population builder
# ---------------------------------------------------------------------------

class PopulationBuildRequest(BaseModel):
    mix: dict[str, float]
    total_agents: int = 100
    default_collateral: int | float = 10_000_000_000_000
    collateral_token: TokenSpecSchema = TokenSpecSchema(id="COLLATERAL", symbol="COL", decimals=9)
    role_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    seed: int = 42


class PopulationBuildResponse(BaseModel):
    agents: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Sweep execution
# ---------------------------------------------------------------------------

class SweepRunRequest(BaseModel):
    spec: dict[str, Any]
    param_grid: dict[str, list[Any]]
    num_runs: int = 5
    seeds: list[int] | None = None
    master_seed: int | None = None
    metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SweepGateRequest(BaseModel):
    data: list[dict[str, Any]]
    checks: dict[str, dict[str, Any]]


class SweepGateResponse(BaseModel):
    passed: bool
    results: dict[str, bool]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    data: list[dict[str, Any]]
    fields: list[str] | None = None


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------

class OrderBookResponse(BaseModel):
    simulation_id: str
    books: dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------

class PredicateSchema(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    children: list["PredicateSchema"] | None = None
    child: "PredicateSchema | None" = None


class PredicateEvalRequest(BaseModel):
    predicate: PredicateSchema
    market_state: dict[str, Any] = Field(default_factory=dict)
    agent_state: dict[str, Any] = Field(default_factory=dict)


class PredicateEvalResponse(BaseModel):
    result: bool


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


# ---------------------------------------------------------------------------
# Action schemas (mirror defi_sim.core.types.Action and its subclasses)
# ---------------------------------------------------------------------------


class ActionSchema(BaseModel):
    """Pydantic mirror of ``defi_sim.core.types.Action``.

    Carries Solana fee fields (``num_required_signatures``,
    ``compute_unit_limit``, ``compute_unit_price_micro_lamports``).
    """

    agent_id: str | int
    num_required_signatures: int = 1
    compute_unit_limit: int | None = None
    compute_unit_price_micro_lamports: int | None = None

    def to_engine_kwargs(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "num_required_signatures": self.num_required_signatures,
            "compute_unit_limit": self.compute_unit_limit,
            "compute_unit_price_micro_lamports": self.compute_unit_price_micro_lamports,
        }

    def to_engine(self) -> Action:
        return Action(**self.to_engine_kwargs())

    @classmethod
    def from_engine(cls, action: Action) -> "ActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
        )


class SwapActionSchema(ActionSchema):
    token_in: str = ""
    token_out: str = ""
    amount_in: int | float = 0

    def to_engine(self) -> SwapAction:
        return SwapAction(
            **self.to_engine_kwargs(),
            token_in=self.token_in,
            token_out=self.token_out,
            amount_in=self.amount_in,
        )

    @classmethod
    def from_engine(cls, action: SwapAction) -> "SwapActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
            token_in=action.token_in,
            token_out=action.token_out,
            amount_in=action.amount_in,
        )


class SingleAssetActionSchema(ActionSchema):
    asset: str = ""
    collateral: str = ""
    amount: int | float = 0
    side: Side = Side.BUY

    def to_engine(self) -> SingleAssetAction:
        return SingleAssetAction(
            **self.to_engine_kwargs(),
            asset=self.asset,
            collateral=self.collateral,
            amount=self.amount,
            side=Side(self.side) if not isinstance(self.side, Side) else self.side,
        )

    @classmethod
    def from_engine(cls, action: SingleAssetAction) -> "SingleAssetActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
            asset=action.asset,
            collateral=action.collateral,
            amount=action.amount,
            side=action.side,
        )


class BundleActionSchema(ActionSchema):
    collateral: str = ""
    amount: int | float = 0
    weights: dict[str, int | float] = Field(default_factory=dict)
    side: Side = Side.BUY
    mu: float | None = None
    sigma: float | None = None

    def to_engine(self) -> BundleAction:
        return BundleAction(
            **self.to_engine_kwargs(),
            collateral=self.collateral,
            amount=self.amount,
            weights=dict(self.weights),
            side=Side(self.side) if not isinstance(self.side, Side) else self.side,
            mu=self.mu,
            sigma=self.sigma,
        )

    @classmethod
    def from_engine(cls, action: BundleAction) -> "BundleActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
            collateral=action.collateral,
            amount=action.amount,
            weights=dict(action.weights),
            side=action.side,
            mu=action.mu,
            sigma=action.sigma,
        )


class LPActionSchema(ActionSchema):
    collateral: str = ""
    amount: int | float = 0
    lp_type: LPActionType = LPActionType.DEPOSIT
    target_weights: dict[str, int | float] | None = None
    price_range: tuple[int | float, int | float] | None = None
    position_id: str | None = None

    def to_engine(self) -> LPAction:
        return LPAction(
            **self.to_engine_kwargs(),
            collateral=self.collateral,
            amount=self.amount,
            lp_type=LPActionType(self.lp_type) if not isinstance(self.lp_type, LPActionType) else self.lp_type,
            target_weights=dict(self.target_weights) if self.target_weights is not None else None,
            price_range=self.price_range,
            position_id=self.position_id,
        )

    @classmethod
    def from_engine(cls, action: LPAction) -> "LPActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
            collateral=action.collateral,
            amount=action.amount,
            lp_type=action.lp_type,
            target_weights=dict(action.target_weights) if action.target_weights is not None else None,
            price_range=action.price_range,
            position_id=action.position_id,
        )


class OrderActionSchema(ActionSchema):
    base: str = ""
    quote: str = ""
    side: OrderSide = OrderSide.BUY
    price: int | float = 0
    quantity: int | float = 0

    def to_engine(self) -> OrderAction:
        return OrderAction(
            **self.to_engine_kwargs(),
            base=self.base,
            quote=self.quote,
            side=OrderSide(self.side) if not isinstance(self.side, OrderSide) else self.side,
            price=self.price,
            quantity=self.quantity,
        )

    @classmethod
    def from_engine(cls, action: OrderAction) -> "OrderActionSchema":
        return cls(
            agent_id=action.agent_id,
            num_required_signatures=action.num_required_signatures,
            compute_unit_limit=action.compute_unit_limit,
            compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
            base=action.base,
            quote=action.quote,
            side=action.side,
            price=action.price,
            quantity=action.quantity,
        )
