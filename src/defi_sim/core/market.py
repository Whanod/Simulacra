"""Market ABCs and mixins.

Market — minimal ABC, no pricing or depth assumptions.
PricedMarket — mixin for markets that expose prices.
LiquidityPool — mixin for markets with pooled liquidity.
ConcentratedLiquidityPool — extended mixin for tick-based concentrated liquidity.
LendingMarket — mixin for lending/borrowing protocols.
DerivativesMarket — mixin for perpetuals, futures, options.
Liquidatable — mixin for markets with liquidation mechanics.
"""

from __future__ import annotations

import importlib
from functools import partial
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from defi_sim.core.types import (
    Action,
    AgentId,
    ExecutionContext,
    ExecutionResult,
    MarketSnapshot,
    Numeric,
    PositionSide,
    TokenId,
)


# ---------------------------------------------------------------------------
# Market type registry for serialization
# ---------------------------------------------------------------------------

_MARKET_REGISTRY: dict[str, type["Market"]] = {}


def register_market_type(cls: type["Market"]) -> type["Market"]:
    """Class decorator: register a Market subclass for snapshot deserialization."""
    _MARKET_REGISTRY[cls.market_type] = cls
    return cls


def get_market_registry() -> dict[str, type["Market"]]:
    return _MARKET_REGISTRY


def serialize_callable_ref(fn: Any) -> dict[str, str] | None:
    """Serialize a top-level callable by import path when possible."""
    if fn is None:
        return None
    if isinstance(fn, partial):
        base_ref = serialize_callable_ref(fn.func)
        if base_ref is None:
            return None
        return {
            "kind": "partial",
            "func": base_ref,
            "args": list(fn.args),
            "keywords": dict(fn.keywords or {}),
        }
    module_name = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if not module_name or not qualname or "<locals>" in qualname or "<lambda>" in qualname:
        return None
    return {"kind": "callable", "module": module_name, "qualname": qualname}


def deserialize_callable_ref(ref: dict[str, str] | None) -> Any:
    if ref is None:
        return None
    if ref.get("kind") == "partial":
        return partial(
            deserialize_callable_ref(ref["func"]),
            *ref.get("args", []),
            **ref.get("keywords", {}),
        )
    obj = importlib.import_module(ref["module"])
    for part in ref["qualname"].split("."):
        obj = getattr(obj, part)
    return obj


# ---------------------------------------------------------------------------
# Market ABC
# ---------------------------------------------------------------------------


class Market(ABC):
    market_type: ClassVar[str]
    fee_model: Any = None  # FeeModel | None

    @abstractmethod
    def get_state(self) -> MarketSnapshot: ...

    @abstractmethod
    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult: ...

    @abstractmethod
    def copy(self) -> "Market": ...

    @abstractmethod
    def to_bytes(self) -> bytes: ...

    @classmethod
    @abstractmethod
    def from_bytes(cls, data: bytes) -> "Market": ...

    def get_fee_model(self, default: Any) -> Any:
        """Return this market's fee model, falling back to the engine default."""
        return self.fee_model if self.fee_model is not None else default


# ---------------------------------------------------------------------------
# PricedMarket mixin
# ---------------------------------------------------------------------------


class PricedMarket(ABC):
    """Mixin for markets that have a meaningful price per asset."""

    @abstractmethod
    def get_prices(self) -> dict[TokenId, Numeric]: ...

    @abstractmethod
    def get_depth(self, token: TokenId) -> Numeric: ...


# ---------------------------------------------------------------------------
# LiquidityPool mixin
# ---------------------------------------------------------------------------


@dataclass
class LPPosition:
    """Per-agent LP position within a pool."""
    agent_id: AgentId
    position_id: str = ""
    deposited: Numeric = 0
    share_fraction: Numeric = 0
    accumulated_fees: Numeric = 0


@dataclass
class LPState:
    """Snapshot of the liquidity pool state."""
    total_deposited: Numeric = 0
    accumulated_fees: Numeric = 0
    effective_liquidity: Numeric = 0
    num_lps: int = 0


class LiquidityPool(ABC):
    """Mixin for markets that support pooled liquidity (AMMs)."""

    @abstractmethod
    def deposit_liquidity(self, agent_id: AgentId, amount: Numeric,
                          weights: dict[TokenId, Numeric] | None = None,
                          price_range: tuple[Numeric, Numeric] | None = None,
                          position_id: str | None = None) -> ExecutionResult: ...

    @abstractmethod
    def withdraw_liquidity(self, agent_id: AgentId, amount: Numeric,
                           position_id: str | None = None) -> ExecutionResult: ...

    @abstractmethod
    def get_lp_state(self) -> LPState: ...

    @abstractmethod
    def get_lp_position(self, agent_id: AgentId,
                        position_id: str | None = None) -> LPPosition | None: ...

    @abstractmethod
    def get_all_lp_positions(self) -> list[LPPosition]: ...

    @abstractmethod
    def reset_accumulated_fees(self) -> Numeric: ...


# ---------------------------------------------------------------------------
# ConcentratedLiquidityPool mixin
# ---------------------------------------------------------------------------


@dataclass
class ConcentratedLPPosition(LPPosition):
    tick_lower: Numeric = 0
    tick_upper: Numeric = 0
    liquidity: Numeric = 0
    in_range: bool = True


class ConcentratedLiquidityPool(LiquidityPool):
    """Extended mixin for pools with tick-based concentrated liquidity."""

    @abstractmethod
    def get_liquidity_in_range(self, tick_lower: Numeric,
                                tick_upper: Numeric) -> Numeric: ...

    @abstractmethod
    def get_active_liquidity(self) -> Numeric: ...

    @abstractmethod
    def get_positions_in_range(self, tick_lower: Numeric,
                                tick_upper: Numeric) -> list[ConcentratedLPPosition]: ...

    @abstractmethod
    def get_current_tick(self) -> Numeric: ...


# ---------------------------------------------------------------------------
# LendingMarket mixin
# ---------------------------------------------------------------------------


@dataclass
class BorrowPosition:
    collateral: dict[TokenId, Numeric] = field(default_factory=dict)
    borrows: dict[TokenId, Numeric] = field(default_factory=dict)
    health_factor: Numeric = 0


class LendingMarket(ABC):
    """Mixin for markets that support collateralized borrowing."""

    @abstractmethod
    def get_position(self, agent_id: AgentId) -> BorrowPosition | None: ...

    @abstractmethod
    def get_interest_rate(self, token: TokenId) -> Numeric: ...

    @abstractmethod
    def get_utilization(self, token: TokenId) -> Numeric: ...

    @abstractmethod
    def accrue_interest(self, elapsed_seconds: int) -> None: ...


# ---------------------------------------------------------------------------
# DerivativesMarket mixin
# ---------------------------------------------------------------------------


@dataclass
class DerivativePosition:
    agent_id: AgentId = ""
    token: TokenId = ""
    side: PositionSide = PositionSide.LONG
    size: Numeric = 0
    entry_price: Numeric = 0
    margin: Numeric = 0
    unrealized_pnl: Numeric = 0
    liquidation_price: Numeric = 0


class DerivativesMarket(ABC):
    """Mixin for markets with leveraged derivative positions."""

    @abstractmethod
    def get_position(self, agent_id: AgentId, token: TokenId) -> DerivativePosition | None: ...

    @abstractmethod
    def get_all_positions(self) -> list[DerivativePosition]: ...

    @abstractmethod
    def get_funding_rate(self, token: TokenId) -> Numeric: ...

    @abstractmethod
    def get_open_interest(self, token: TokenId) -> tuple[Numeric, Numeric]: ...

    @abstractmethod
    def settle_funding(self, elapsed_seconds: int) -> list[tuple[AgentId, Numeric]]: ...

    @abstractmethod
    def get_mark_price(self, token: TokenId) -> Numeric: ...


# ---------------------------------------------------------------------------
# Liquidatable mixin
# ---------------------------------------------------------------------------


class Liquidatable(ABC):
    """Mixin for markets where positions can be liquidated."""

    @abstractmethod
    def get_liquidatable_agents(self) -> list[AgentId]: ...

    @abstractmethod
    def compute_liquidation_bonus(self, agent_id: AgentId, repay_amount: Numeric) -> Numeric: ...
