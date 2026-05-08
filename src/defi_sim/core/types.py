"""Core types for the defi-sim library.

Token model, agent identity, numeric modes, action hierarchy,
predicate hierarchy, state types.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal

from defi_sim._compat import msgpack

if TYPE_CHECKING:
    from defi_sim.engine.bundle import TipPayment as _TipPayment
else:
    _TipPayment = Any  # noqa: F811 — runtime placeholder (PRD US-011)

# ---------------------------------------------------------------------------
# Numeric mode
# ---------------------------------------------------------------------------

Numeric = int | float

TokenId = str
AgentId = str | int
BlockHash = str

_BIGINT_MARKER = "__defi_sim_bigint__"


def encode_msgpack_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: encode_msgpack_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [encode_msgpack_value(inner) for inner in value]
    if isinstance(value, tuple):
        return [encode_msgpack_value(inner) for inner in value]
    if isinstance(value, int) and not (-(2**63) <= value <= (2**64 - 1)):
        return {_BIGINT_MARKER: str(value)}
    return value


def decode_msgpack_value(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {_BIGINT_MARKER}:
            return int(value[_BIGINT_MARKER])
        return {key: decode_msgpack_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [decode_msgpack_value(inner) for inner in value]
    return value


@dataclass(frozen=True)
class NumericMode:
    """Controls whether the simulation uses fixed-point integers or floats."""
    use_float: bool = False

    @property
    def zero(self) -> Numeric:
        return 0.0 if self.use_float else 0

    def scale(self, value: float, token: "Token") -> Numeric:
        if self.use_float:
            return value
        return int(value * token.scale)

    def unscale(self, value: Numeric, token: "Token") -> float:
        if self.use_float:
            return float(value)
        return int(value) / token.scale


FIXED_POINT = NumericMode(use_float=False)
FLOAT_MODE = NumericMode(use_float=True)

# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """Represents a distinct token / asset in the simulation."""
    id: TokenId
    symbol: str
    decimals: int = 18

    @property
    def scale(self) -> int:
        return 10 ** self.decimals

    def to_scaled(self, value: float) -> int:
        return int(value * self.scale)

    def from_scaled(self, value: int) -> float:
        return value / self.scale


def make_index_tokens(num_assets: int, decimals: int = 9) -> list[Token]:
    """Create tokens named '0', '1', ..., 'N-1' with uniform decimals."""
    return [Token(id=str(i), symbol=f"T{i}", decimals=decimals) for i in range(num_assets)]


COLLATERAL = Token(id="COLLATERAL", symbol="COL", decimals=9)

# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentRole:
    """Metadata tag for agent classification."""
    name: str
    tags: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class LPActionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    REBALANCE = "rebalance"


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


class MarginDirection(Enum):
    ADD = "add"
    REMOVE = "remove"


class GovernanceActionType(Enum):
    PROPOSE = "propose"
    VOTE = "vote"
    EXECUTE = "execute"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


# ---------------------------------------------------------------------------
# Action hierarchy
# ---------------------------------------------------------------------------


# Solana priority-fee math is `priority_lamports = ceil(price_micro * cu_limit / 1_000_000)`.
# Used as the cu_limit when an action sets a price but no explicit limit
# (per-action-type registry lands in task 0.4.3).
DEFAULT_CU_LIMIT_FALLBACK: int = 200_000


@dataclass
class Action:
    """Base action.

    Solana fee fields drive the real mainnet fee formula:
    `5_000 * num_required_signatures + ceil(price_micro * cu_limit / 1_000_000)`.
    """
    agent_id: AgentId
    num_required_signatures: int = 1
    compute_unit_limit: int | None = None
    compute_unit_price_micro_lamports: int | None = None
    submission_path: Literal["rpc", "tpu_quic", "jito_relayer"] = "rpc"
    # Oracle accounts this action needs to read (PRD US-006 line 491).
    # Markets surface these into ``LockedAction.read_locks`` during
    # ``resolve_locks`` so the parallel scheduler models oracle-account
    # contention correctly. Empty by default — only consumers that
    # actually consult an oracle populate this.
    oracle_account_ids: frozenset[str] = field(default_factory=frozenset)
    # PRD US-014 line 1096: recent_blockhash references the slot whose blockhash
    # this action was signed against. ``None`` means "use the engine's latest".
    # ``expiry_slot`` defaults to ``blockhash_slot + 150`` when unset (matching
    # Solana mainnet's ~150-slot blockhash validity window).
    recent_blockhash: BlockHash | None = None
    expiry_slot: int | None = None

    def set_compute_unit_limit(self, limit: int) -> "Action":
        self.compute_unit_limit = int(limit)
        return self

    def set_compute_unit_price(self, price_micro_lamports: int) -> "Action":
        self.compute_unit_price_micro_lamports = int(price_micro_lamports)
        return self

    def priority_lamports(self) -> int:
        """Lamport-equivalent priority fee for this action.

        Uses resolved-with-defaults CU values: an unset `compute_unit_limit`
        falls back to `DEFAULT_CU_LIMIT_FALLBACK` (per-action-type registry
        lands in task 0.4.3); an unset price defaults to 0.
        """
        cu_limit = self.compute_unit_limit if self.compute_unit_limit is not None else DEFAULT_CU_LIMIT_FALLBACK
        price_micro = self.compute_unit_price_micro_lamports or 0
        if price_micro <= 0 or cu_limit <= 0:
            return 0
        return math.ceil(price_micro * cu_limit / 1_000_000)

    def validator_reward_lamports(self) -> int:
        """Validator's revenue from this action: priority fee + half of base fee."""
        num_signers = self.num_required_signatures or 1
        base_fee = 5_000 * num_signers
        validator_base = base_fee - (base_fee // 2)
        return self.priority_lamports() + validator_base


# --- Trading ---

@dataclass
class SwapAction(Action):
    """Swap one token for another."""
    token_in: TokenId = ""
    token_out: TokenId = ""
    amount_in: Numeric = 0


@dataclass
class SingleAssetAction(Action):
    """Buy or sell a single asset for collateral."""
    asset: TokenId = ""
    collateral: TokenId = ""
    amount: Numeric = 0
    side: Side = Side.BUY


@dataclass
class BundleAction(Action):
    """Trade a weighted bundle across multiple assets."""
    collateral: TokenId = ""
    amount: Numeric = 0
    weights: dict[TokenId, Numeric] = field(default_factory=dict)
    side: Side = Side.BUY
    mu: float | None = None
    sigma: float | None = None


# --- Liquidity ---

@dataclass
class LPAction(Action):
    """Deposit, withdraw, or rebalance liquidity in a pool."""
    collateral: TokenId = ""
    amount: Numeric = 0
    lp_type: LPActionType = LPActionType.DEPOSIT
    target_weights: dict[TokenId, Numeric] | None = None
    price_range: tuple[Numeric, Numeric] | None = None
    position_id: str | None = None


# --- Order book ---

@dataclass
class OrderAction(Action):
    """Place a limit order on a CLOB."""
    base: TokenId = ""
    quote: TokenId = ""
    side: OrderSide = OrderSide.BUY
    price: Numeric = 0
    quantity: Numeric = 0


# --- Lending / Borrowing ---

@dataclass
class DepositCollateralAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


@dataclass
class WithdrawCollateralAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


@dataclass
class BorrowAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


@dataclass
class RepayAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


# --- Liquidation ---

@dataclass
class LiquidateAction(Action):
    target_agent_id: AgentId = ""
    repay_token: TokenId = ""
    repay_amount: Numeric = 0
    seize_token: TokenId = ""


# --- Derivatives ---

@dataclass
class OpenPositionAction(Action):
    token: TokenId = ""
    collateral: TokenId = ""
    size: Numeric = 0
    side: PositionSide = PositionSide.LONG
    leverage: Numeric = 1


@dataclass
class ClosePositionAction(Action):
    token: TokenId = ""
    size: Numeric | None = None


@dataclass
class AdjustMarginAction(Action):
    token: TokenId = ""
    collateral: TokenId = ""
    amount: Numeric = 0
    direction: MarginDirection = MarginDirection.ADD


# --- Staking ---

@dataclass
class StakeAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


@dataclass
class UnstakeAction(Action):
    token: TokenId = ""
    amount: Numeric = 0


@dataclass
class ClaimRewardsAction(Action):
    pool_id: str | None = None


# --- Governance ---

@dataclass
class GovernanceAction(Action):
    action_type: GovernanceActionType = GovernanceActionType.PROPOSE
    proposal_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


# --- Composability ---

@dataclass
class AtomicAction(Action):
    """Execute multiple actions atomically. If any sub-action fails,
    all previous sub-actions in this batch are reverted."""
    actions: list[Action] = field(default_factory=list)


@dataclass
class FlashLoanAction(Action):
    """Borrow tokens with zero collateral, execute inner actions,
    repay in the same atomic batch. Reverts if repayment fails."""
    token: TokenId = ""
    amount: Numeric = 0
    inner_actions: list[Action] = field(default_factory=list)


@dataclass
class MultiMarketAction(Action):
    """Wraps any Action with a market target, used in World multi-market mode."""
    market_name: str = ""
    inner: Action = field(default_factory=lambda: Action(agent_id=""))


# --- Conditional ---

@dataclass
class ConditionalAction(Action):
    """Execute inner action only if predicate returns True."""
    predicate: "Predicate | None" = None
    inner: Action = field(default_factory=lambda: Action(agent_id=""))


# ---------------------------------------------------------------------------
# Predicate hierarchy
# ---------------------------------------------------------------------------


def _resolve_field(obj: Any, path: str) -> Any:
    """Resolve a dot-separated field path on an object or dict."""
    parts = path.split(".")
    i = 0
    while i < len(parts):
        part = parts[i]
        # Support the documented shorthand "balance.USDC" in addition to
        # direct access via "balances.USDC".
        if part == "balance" and hasattr(obj, "balance") and callable(getattr(obj, "balance")):
            if i + 1 >= len(parts):
                raise KeyError("balance path requires a token identifier")
            obj = obj.balance(parts[i + 1])
            i += 2
            continue
        if isinstance(obj, dict):
            obj = obj[part]
        else:
            obj = getattr(obj, part)
        i += 1
    return obj


def _compare(value: Any, op: str, threshold: Any) -> bool:
    ops = {
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
    }
    return ops[op](value, threshold)


class Predicate(ABC):
    """Base class for serializable predicates."""

    @abstractmethod
    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool: ...

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> "Predicate": ...


@dataclass(frozen=True)
class ThresholdPredicate(Predicate):
    """True when a numeric field crosses a threshold."""
    field: str = ""
    source: str = "market"
    op: str = "<"
    threshold: Numeric = 0

    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool:
        obj = market_state if self.source == "market" else agent_state
        value = _resolve_field(obj, self.field)
        return _compare(value, self.op, self.threshold)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "threshold", "field": self.field, "source": self.source,
                "op": self.op, "threshold": self.threshold}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThresholdPredicate":
        return cls(field=data["field"], source=data["source"],
                   op=data["op"], threshold=data["threshold"])


@dataclass(frozen=True)
class AndPredicate(Predicate):
    children: tuple[Predicate, ...] = ()

    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool:
        return all(c.evaluate(market_state, agent_state) for c in self.children)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "and", "children": [c.to_dict() for c in self.children]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AndPredicate":
        children = tuple(_predicate_from_dict(c) for c in data["children"])
        return cls(children=children)


@dataclass(frozen=True)
class OrPredicate(Predicate):
    children: tuple[Predicate, ...] = ()

    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool:
        return any(c.evaluate(market_state, agent_state) for c in self.children)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "or", "children": [c.to_dict() for c in self.children]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrPredicate":
        children = tuple(_predicate_from_dict(c) for c in data["children"])
        return cls(children=children)


@dataclass(frozen=True)
class NotPredicate(Predicate):
    child: Predicate = field(default_factory=lambda: ThresholdPredicate())

    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool:
        return not self.child.evaluate(market_state, agent_state)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "not", "child": self.child.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotPredicate":
        return cls(child=_predicate_from_dict(data["child"]))


class LambdaPredicate(Predicate):
    """Escape hatch for arbitrary callables. NOT serializable."""

    def __init__(self, fn: Callable[..., bool]):
        self._fn = fn

    def evaluate(self, market_state: Any, agent_state: "AgentState") -> bool:
        return self._fn(market_state, agent_state)

    def to_dict(self) -> dict[str, Any]:
        raise TypeError("LambdaPredicate is not serializable")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LambdaPredicate":
        raise TypeError("LambdaPredicate cannot be deserialized")


_PREDICATE_REGISTRY: dict[str, type[Predicate]] = {
    "threshold": ThresholdPredicate,
    "and": AndPredicate,
    "or": OrPredicate,
    "not": NotPredicate,
}


def _predicate_from_dict(data: dict[str, Any]) -> Predicate:
    cls = _PREDICATE_REGISTRY[data["type"]]
    return cls.from_dict(data)


def when(field: str, op: str, threshold: Numeric, source: str = "market") -> ThresholdPredicate:
    """Shorthand: when("health_factor", "<", 1.2, source="agent")"""
    return ThresholdPredicate(field=field, source=source, op=op, threshold=threshold)


# ---------------------------------------------------------------------------
# State types
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    """Returned by Market.execute(). Describes what happened."""
    success: bool
    token_deltas: dict[TokenId, Numeric] = field(default_factory=dict)
    other_agent_deltas: dict[AgentId, dict[TokenId, Numeric]] = field(default_factory=dict)
    fees_paid: Numeric = 0
    fee_splits: dict[str, Numeric] = field(default_factory=dict)
    fee_token: TokenId | None = None
    volume: Numeric | None = None
    other_agent_volumes: dict[AgentId, Numeric] = field(default_factory=dict)
    error: str | None = None


@dataclass
class AgentState:
    agent_id: AgentId
    role: AgentRole = field(default_factory=lambda: AgentRole("unknown"))
    balances: dict[TokenId, Numeric] = field(default_factory=dict)
    cumulative_volume: Numeric = 0
    realized_pnl: Numeric = 0

    def balance(self, token: TokenId) -> Numeric:
        return self.balances.get(token, 0)

    def to_bytes(self) -> bytes:
        return msgpack.packb(encode_msgpack_value({
            "agent_id": self.agent_id,
            "role_name": self.role.name,
            "role_tags": list(self.role.tags),
            "balances": self.balances,
            "cumulative_volume": self.cumulative_volume,
            "realized_pnl": self.realized_pnl,
        }), use_bin_type=True)

    @classmethod
    def from_bytes(cls, data: bytes) -> "AgentState":
        d = decode_msgpack_value(msgpack.unpackb(data, raw=False, strict_map_key=False))
        return cls(
            agent_id=d["agent_id"],
            role=AgentRole(name=d["role_name"], tags=frozenset(d["role_tags"])),
            balances=d["balances"],
            cumulative_volume=d["cumulative_volume"],
            realized_pnl=d["realized_pnl"],
        )


@dataclass
class ExecutionContext:
    """Carries state the market needs for validation and fee computation."""
    agent_state: AgentState
    current_round: int = 0
    total_rounds: int = 200
    timestamp: int | float = 0
    market_state: "MarketSnapshot | None" = None
    numeric_mode: NumericMode = field(default_factory=lambda: FIXED_POINT)
    default_fee_model: Any = None  # FeeModel | None
    execution_cost: Numeric = 0
    gas_cost: Numeric | None = None
    parameters: Any = None  # ParameterStore | None

    def __post_init__(self) -> None:
        if self.gas_cost is not None and self.execution_cost == 0:
            self.execution_cost = self.gas_cost
        self.gas_cost = self.execution_cost


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass
class MarketSnapshot:
    """Base snapshot. Every market returns at least this."""
    num_assets: int = 0
    tokens: list[TokenId] = field(default_factory=list)


@dataclass
class AmmSnapshot(MarketSnapshot):
    reserves: dict[TokenId, Numeric] = field(default_factory=dict)
    prices: dict[TokenId, Numeric] = field(default_factory=dict)
    total_liquidity: Numeric = 0
    invariant: Numeric = 0
    # Live swap fee in basis points. None when the market either has no fee
    # surface or its fee model is state-dependent (dynamic / spread / tiered);
    # only flat-fee surfaces should populate this so fee-elastic agents read a
    # stable value. Whirlpool exposes ``pool.fee_rate / 100``.
    fee_bps: float | None = None


@dataclass
class ClobSnapshot(MarketSnapshot):
    best_bid: dict[TokenId, Numeric | None] = field(default_factory=dict)
    best_ask: dict[TokenId, Numeric | None] = field(default_factory=dict)
    spread: dict[TokenId, Numeric] = field(default_factory=dict)
    total_depth: dict[TokenId, Numeric] = field(default_factory=dict)


@dataclass
class LendingSnapshot(MarketSnapshot):
    total_deposits: dict[TokenId, Numeric] = field(default_factory=dict)
    total_borrows: dict[TokenId, Numeric] = field(default_factory=dict)
    utilization: dict[TokenId, Numeric] = field(default_factory=dict)
    interest_rates: dict[TokenId, Numeric] = field(default_factory=dict)


@dataclass
class DerivativesSnapshot(MarketSnapshot):
    open_interest_long: dict[TokenId, Numeric] = field(default_factory=dict)
    open_interest_short: dict[TokenId, Numeric] = field(default_factory=dict)
    funding_rate: dict[TokenId, Numeric] = field(default_factory=dict)
    mark_price: dict[TokenId, Numeric] = field(default_factory=dict)
    index_price: dict[TokenId, Numeric] = field(default_factory=dict)


@dataclass
class ValidatorEpochRevenue:
    """Per-(epoch, validator) MEV revenue accumulator (PRD US-012 line 969).

    Tracks bundle-tip revenue credited to a validator over a single epoch.
    ``validator_revenue_lamports`` is the validator-side cut and
    ``stake_pool_revenue_lamports`` is the JitoSOL stake-pool inflow that
    routed to the validator's configured ``stake_pool_address``. Vanilla
    validators do not accrue MEV revenue and never appear here.
    """

    epoch: int
    pubkey: str
    client: Literal["jito_solana", "vanilla"]
    validator_revenue_lamports: int = 0
    stake_pool_revenue_lamports: int = 0


@dataclass(frozen=True)
class BundleOutcome:
    """Per-bundle outcome surfaced on the run snapshot (PRD US-011 line 891).

    Status is one of:
      * ``landed`` — selected by the auction and executed without revert.
      * ``reverted`` — selected, executed, but rolled back at
        ``failed_at_index`` (atomic rollback per PRD US-005 / US-011 line 838).
      * ``dropped`` — admitted-but-not-selected by the auction, with
        ``drop_reason`` from :class:`BundleDropReason`.

    Tip and revenue split are reported in lamports. Validator + stake-pool
    revenue are zero on revert/drop; on land they sum to the *paid* tip total
    (which equals the bundle's declared ``tip_lamports`` under atomic
    semantics, or zero if a revert killed every tip — kept for symmetry with
    the land-but-zero-paid-tips case that a future partial-bundle landing
    mode could expose).

    ``alt_ids`` is the union of address-lookup-table references across the
    bundle's transactions (PRD US-011 line 894 — ALT usage per bundle).
    """

    slot: int
    bundle_index: int
    status: Literal["landed", "reverted", "dropped"]
    tip_lamports: int
    validator_revenue_lamports: int
    stake_pool_revenue_lamports: int
    alt_ids: tuple[str, ...] = ()
    num_txs: int = 0
    total_cu: int = 0
    failed_at_index: int | None = None
    drop_reason: str | None = None


@dataclass
class RoundSnapshot:
    round: int = 0
    timestamp: int | float = 0
    epoch: int = 0
    agent_states: dict[AgentId, AgentState] = field(default_factory=dict)
    market_state: MarketSnapshot | None = None
    all_market_states: dict[str, MarketSnapshot] | None = None
    # Solana-native slot metadata. Populated only when the engine runs
    # under a SolanaSlotClock; None on integer-block clocks so non-Solana
    # snapshots round-trip unchanged.
    current_slot: int | None = None
    current_leader: str | None = None
    # PRD US-011 line 891: per-slot bundle outcomes (selected + dropped),
    # populated when the execution model is ``SolanaLikeExecution`` with a
    # ``BundleAuction`` configured. Empty list on every other host.
    bundle_outcomes: list[BundleOutcome] = field(default_factory=list)
    # PRD US-012 line 973: nested metrics namespace. Currently carries
    # ``validator_revenue`` -> ``dict[int, dict[str, ValidatorEpochRevenue]]``
    # (epoch -> pubkey -> entry) when at least one ``Validator`` agent has
    # accrued tip revenue. Empty dict on every other host.
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Slot events (Solana-native)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotEvent:
    """A normal slot tick produced by SolanaSlotClock.tick()."""
    slot: int


@dataclass(frozen=True)
class SlotSkippedEvent:
    """A skipped slot tick produced by SolanaSlotClock.tick().

    `scheduled_leader` is the leader pubkey that was supposed to produce
    this slot, or None when no LeaderSchedule is wired."""
    slot: int
    scheduled_leader: str | None = None


@dataclass(frozen=True)
class PriorityFeeMarketUpdatedEvent:
    """Emitted per slot per account whose priority-fee distribution moved
    by more than the configured threshold (default 5%) since the last
    update for that account (PRD US-010 line 745).

    ``percentiles`` is the post-update distribution keyed by percentile
    (25, 50, 75, 90, 99) in micro-lamports per CU. ``previous_percentiles``
    is the prior distribution used for the relative-change comparison;
    ``None`` on the first update for an account.
    """
    slot: int
    account_id: str
    percentiles: dict[int, int]
    previous_percentiles: dict[int, int] | None
    threshold: float


@dataclass(frozen=True)
class BlockhashExpiredEvent:
    """Emitted when an action is dropped at admit-time because its
    ``recent_blockhash`` is older than the engine's rolling
    blockhash-validity window (PRD US-014 line 1108).

    ``blockhash`` is the offending blockhash referenced by the action;
    ``slot`` is the slot at which the admit-time check ran.
    """
    slot: int
    action: "Action"
    blockhash: BlockHash


@dataclass(frozen=True)
class ForkReorgEvent:
    """Emitted when the engine rolls a fork at a slot and reorgs the last
    ``depth`` slots (PRD US-014 line 1117).

    ``fork_point_slot`` is the slot at which the fork was rolled (i.e.
    ``ctx.slot`` at the time the per-slot Bernoulli sample fired).
    ``depth`` is the number of slots flagged "abandoned" — the engine
    drops state transitions for slots ``[fork_point_slot - depth + 1,
    fork_point_slot]``. ``abandoned_bundle_ids`` and
    ``abandoned_actions_count`` summarise what was reverted.
    """
    fork_point_slot: int
    depth: int
    abandoned_bundle_ids: tuple[str, ...]
    abandoned_actions_count: int


@dataclass(frozen=True)
class BundleTipPaidEvent:
    """Emitted when a bundle lands and pays its tip (PRD US-011 line 839).

    Mirrors the per-bundle outcome the auction's pre-stage records. The
    payload carries position-aware ``tip_payments`` (with ``tx_index``
    and ``location``) per PRD line 839 so US-012 validator economics and
    bundle-replay paths can route lamports without losing the tip's
    bundle position. ``tip_recipients`` is preserved as a derived tuple
    of recipient pubkeys for cheap pubkey-only consumers.
    """
    slot: int
    bundle_index: int
    leader_pubkey: str | None
    tip_lamports: int
    tip_payments: tuple[_TipPayment, ...]
    jito_stake_pool_share: float
    # PRD US-013 line 1049: searcher attribution. Populated when a
    # JitoSearcher submitted the bundle via ctx.submit_bundle; ``None``
    # for direct execution.submit_bundle callers.
    searcher_id: str | None = None
    strategy: str | None = None

    @property
    def tip_recipients(self) -> tuple[str, ...]:
        return tuple(tp.recipient for tp in self.tip_payments)


@dataclass(frozen=True)
class BundleTipRevertedEvent:
    """Emitted when a fork rollback retracts a previously-paid tip
    (PRD US-014 line 1124).

    Bus consumers receive ``BundleTipPaid`` synchronously the slot it
    lands; if a later fork at slot ``N`` with depth ``d`` rolls back a
    slot in ``[N - d, N - 1]``, the tip never actually paid. Engine
    state (``_tip_outcomes``, validator balances, validator-revenue
    map, searcher metrics) is restored from the snapshot, but consumers
    that aggregate bus events would otherwise still see ghost tips —
    this retraction event lets them debit a previously-emitted
    ``BundleTipPaid`` payload.

    ``original`` carries the same payload that was previously emitted
    so consumers can match on ``slot``/``bundle_index``/``searcher_id``
    without keeping their own index. ``fork_point_slot`` /
    ``reorg_depth`` mirror the corresponding ``ForkReorgEvent``.
    """
    fork_point_slot: int
    reorg_depth: int
    original: "BundleTipPaidEvent"


@dataclass(frozen=True)
class ComputeBudgetExhaustedEvent:
    """Emitted when an action is dropped or deferred because it would
    exceed a Solana compute-budget cap.

    `budget_kind` discriminates which cap was breached. All three values
    are live: `per_tx` is emitted at admit-time, while `per_slot` and
    `per_writable_account` are emitted by ``execute_slot`` as it walks
    the lock-resolved candidates (PRD US-008 line 614).
    """
    slot: int
    offender: AgentId
    action: "Action"
    budget_kind: Literal["per_tx", "per_slot", "per_writable_account"]
    remaining: int
    attempted: int


@dataclass
class SimulationResult:
    """Typed output of SimulationEngine.run()."""
    price_history: list[dict[TokenId, Numeric]] = field(default_factory=list)
    # Per-round fees, keyed by destination (e.g. `lp`, `protocol`, `burn`)
    # then by token — multi-token runs must not collapse different tokens
    # into one scalar (5 USDC + 2 ETH != 7).
    fee_history: list[dict[str, dict[TokenId, Numeric]]] = field(default_factory=list)
    agent_final_states: dict[AgentId, AgentState] = field(default_factory=dict)
    round_snapshots: list[RoundSnapshot] = field(default_factory=list)
    num_rounds: int = 0
    num_rounds_executed: int = 0
    seed: int = 0
    stopped_early: bool = False
    cancelled: bool = False
    stop_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
