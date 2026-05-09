"""L2-Norm CFAMM Market.

Port of quant-simulation models/math_engine.py.
Invariant: Sigma(L - r_i)^2 = L^2

Implements Market + PricedMarket + LiquidityPool.
"""

from __future__ import annotations

import copy
import math
from typing import Any, ClassVar

from defi_sim._compat import msgpack
from defi_sim.core.market import (
    LPPosition,
    LPState,
    LiquidityPool,
    Market,
    PricedMarket,
    deserialize_callable_ref,
    register_market_type,
    serialize_callable_ref,
)
from defi_sim.core.math import isqrt
from defi_sim.engine.scheduler import LockedAction
from defi_sim.core.types import (
    Action,
    AgentId,
    AgentState,
    AmmSnapshot,
    BundleAction,
    decode_msgpack_value,
    encode_msgpack_value,
    ExecutionContext,
    ExecutionResult,
    LPAction,
    LPActionType,
    NumericMode,
    Numeric,
    Side,
    SingleAssetAction,
    SwapAction,
    Token,
    TokenId,
)


@register_market_type
class CfammMarket(Market, PricedMarket, LiquidityPool):
    """Constant-function AMM using L2-norm invariant: Sigma(L - r_i)^2 = L^2"""

    market_type: ClassVar[str] = "cfamm"
    supports_lp_rebalance: ClassVar[bool] = False

    def __init__(
        self,
        tokens: list[Token],
        initial_liquidity: int | float,
        fee_model: Any = None,
        collateral_token: TokenId = "COLLATERAL",
        pool_account_id: str | None = None,
    ):
        self.fee_model = fee_model
        self._collateral_token = collateral_token
        self._pool_account_id_override = pool_account_id
        self._use_float = isinstance(initial_liquidity, float)
        self._tokens = tokens
        self._token_ids = [t.id for t in tokens]
        self._num_assets = len(tokens)

        liq = float(initial_liquidity) if self._use_float else int(initial_liquidity)
        n = self._num_assets
        if self._use_float:
            x_per = liq / math.sqrt(n)
        else:
            x_per = isqrt(liq * liq // n)
        reserve_per = liq - x_per

        self._reserves: dict[TokenId, Numeric] = {
            t.id: reserve_per for t in tokens
        }
        self._total_minted: Numeric = liq
        self._accumulated_fees: Numeric = 0.0 if self._use_float else 0
        self._lp_positions: dict[AgentId, LPPosition] = {}
        self._total_deposited: Numeric = liq

    def configure_numeric_mode(self, numeric_mode: NumericMode) -> None:
        use_float = numeric_mode.use_float
        if use_float == self._use_float:
            return

        if use_float:
            self._reserves = {
                token_id: float(reserve)
                for token_id, reserve in self._reserves.items()
            }
            self._total_minted = float(self._total_minted)
            self._accumulated_fees = float(self._accumulated_fees)
            self._total_deposited = float(self._total_deposited)
            self._lp_positions = {
                agent_id: LPPosition(
                    agent_id=position.agent_id,
                    position_id=position.position_id,
                    deposited=float(position.deposited),
                    share_fraction=float(position.share_fraction),
                    accumulated_fees=float(position.accumulated_fees),
                )
                for agent_id, position in self._lp_positions.items()
            }
        else:
            self._reserves = {
                token_id: int(reserve)
                for token_id, reserve in self._reserves.items()
            }
            self._total_minted = int(self._total_minted)
            self._accumulated_fees = int(self._accumulated_fees)
            self._total_deposited = int(self._total_deposited)
            self._lp_positions = {
                agent_id: LPPosition(
                    agent_id=position.agent_id,
                    position_id=position.position_id,
                    deposited=int(position.deposited),
                    share_fraction=int(position.share_fraction),
                    accumulated_fees=int(position.accumulated_fees),
                )
                for agent_id, position in self._lp_positions.items()
            }

        self._use_float = use_float

    # --- Market ABC ---

    def get_state(self) -> AmmSnapshot:
        prices = self.get_prices()
        return AmmSnapshot(
            num_assets=self._num_assets,
            tokens=list(self._token_ids),
            reserves=dict(self._reserves),
            prices=prices,
            total_liquidity=self._total_minted,
            invariant=self._compute_invariant(),
        )

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        """Dispatch by action type."""
        if isinstance(action, SingleAssetAction):
            return self._execute_single(action, ctx)
        elif isinstance(action, SwapAction):
            return self._execute_swap(action, ctx)
        elif isinstance(action, BundleAction):
            return self._execute_bundle(action, ctx)
        elif isinstance(action, LPAction):
            return self._execute_lp(action, ctx)
        return ExecutionResult(success=False, error=f"Unsupported action type: {type(action).__name__}")

    def _pool_account_id(self) -> str:
        """Synthetic Solana-style account id for this CFAMM pool.

        When ``pool_account_id`` is supplied via market params, return it
        verbatim — this lets specs reference the pool by a stable name
        (e.g., for the Phase 1.5 lighthouse priority-fee pre-roll) and
        is the seam Phase 2 calibration will use to swap in real pool
        addresses (PRD US-003 step 6 / CALIBRATE-2.1).

        Otherwise derive from ``id(self)`` — stable for the lifetime of
        the run and distinct across pools, without requiring the market
        to know its World-level name.
        """
        if self._pool_account_id_override is not None:
            return self._pool_account_id_override
        return f"cfamm:{id(self):x}:pool"

    def _lp_position_account_id(self, agent_id: AgentId, position_id: str | None) -> str:
        suffix = position_id if position_id is not None else "default"
        return f"cfamm:{id(self):x}:lp:{agent_id}:{suffix}"

    def resolve_locks(self, action: Action, state: Any = None) -> LockedAction:
        """Map a CFAMM-routed action to its read/write account locks.

        - ``SwapAction`` / ``SingleAssetAction`` / ``BundleAction``: write
          the pool account.
        - ``LPAction`` (deposit/withdraw/rebalance): write both the pool
          account and the LP-position account.
        Anything else: empty locks (no executable lock state to model).
        """
        pool = self._pool_account_id()
        if isinstance(action, (SwapAction, SingleAssetAction, BundleAction)):
            return LockedAction(
                action=action,
                read_locks=frozenset(),
                write_locks=frozenset({pool}),
            )
        if isinstance(action, LPAction):
            position = self._lp_position_account_id(action.agent_id, action.position_id)
            return LockedAction(
                action=action,
                read_locks=frozenset(),
                write_locks=frozenset({pool, position}),
            )
        return LockedAction(action=action)

    def copy(self) -> "CfammMarket":
        c = CfammMarket.__new__(CfammMarket)
        c.fee_model = self.fee_model
        c._tokens = list(self._tokens)
        c._token_ids = list(self._token_ids)
        c._num_assets = self._num_assets
        c._use_float = self._use_float
        c._collateral_token = self._collateral_token
        c._pool_account_id_override = self._pool_account_id_override
        c._reserves = dict(self._reserves)
        c._total_minted = self._total_minted
        c._accumulated_fees = self._accumulated_fees
        c._lp_positions = {k: copy.copy(v) for k, v in self._lp_positions.items()}
        c._total_deposited = self._total_deposited
        return c

    def to_bytes(self) -> bytes:
        return msgpack.packb(encode_msgpack_value({
            "tokens": [(t.id, t.symbol, t.decimals) for t in self._tokens],
            "reserves": {k: v for k, v in self._reserves.items()},
            "total_minted": self._total_minted,
            "accumulated_fees": self._accumulated_fees,
            "total_deposited": self._total_deposited,
            "use_float": self._use_float,
            "collateral_token": self._collateral_token,
            "pool_account_id": self._pool_account_id_override,
            "fee_model_ref": serialize_callable_ref(self.fee_model),
            "lp_positions": {
                str(k): {
                    "agent_id": v.agent_id,
                    "position_id": v.position_id,
                    "deposited": v.deposited,
                    "share_fraction": v.share_fraction,
                    "accumulated_fees": v.accumulated_fees,
                }
                for k, v in self._lp_positions.items()
            },
        }), use_bin_type=True)

    @classmethod
    def from_bytes(cls, data: bytes) -> "CfammMarket":
        d = decode_msgpack_value(msgpack.unpackb(data, raw=False, strict_map_key=False))
        tokens = [Token(id=t[0], symbol=t[1], decimals=t[2]) for t in d["tokens"]]
        m = cls.__new__(cls)
        m.fee_model = None
        m._tokens = tokens
        m._token_ids = [t.id for t in tokens]
        m._num_assets = len(tokens)
        m._use_float = d.get("use_float", False)
        m._collateral_token = d.get("collateral_token", "COLLATERAL")
        m._pool_account_id_override = d.get("pool_account_id")
        m._reserves = d["reserves"]
        m._total_minted = d["total_minted"]
        m._accumulated_fees = d["accumulated_fees"]
        m._total_deposited = d["total_deposited"]
        m.fee_model = deserialize_callable_ref(d.get("fee_model_ref"))
        m._lp_positions = {}
        for k, v in d.get("lp_positions", {}).items():
            m._lp_positions[v["agent_id"]] = LPPosition(
                agent_id=v["agent_id"],
                position_id=v["position_id"],
                deposited=v["deposited"],
                share_fraction=v["share_fraction"],
                accumulated_fees=v["accumulated_fees"],
            )
        m._refresh_lp_shares()
        return m

    # --- PricedMarket ---

    def get_prices(self) -> dict[TokenId, Numeric]:
        """Implied probabilities: p_i = (L - r_i)^2 / L^2, scaled to token scale."""
        k = self._total_minted
        if k == 0:
            return {tid: 0 for tid in self._token_ids}
        k_sq = k * k
        scale = 1.0 if self._use_float else (self._tokens[0].scale if self._tokens else 10**9)
        prices: dict[TokenId, Numeric] = {}
        for tid in self._token_ids:
            x = k - self._reserves[tid]
            if self._use_float:
                prices[tid] = x * x / k_sq
            else:
                prices[tid] = x * x * scale // k_sq
        return prices

    def get_depth(self, token: TokenId) -> Numeric:
        """Available depth for the given token."""
        return self._reserves.get(token, 0)

    # --- LiquidityPool ---

    def deposit_liquidity(
        self,
        agent_id: AgentId,
        amount: Numeric,
        weights: dict[TokenId, Numeric] | None = None,
        price_range: tuple[Numeric, Numeric] | None = None,
        position_id: str | None = None,
    ) -> ExecutionResult:
        """Deposit collateral, rescaling reserves proportionally."""
        amt = float(amount) if self._use_float else int(amount)
        if amt <= 0:
            return ExecutionResult(success=False, error="deposit amount must be positive")

        old_k = self._total_minted
        if old_k == 0:
            return ExecutionResult(success=False, error="cannot deposit into empty pool")

        # Rescale reserves proportionally
        new_k = old_k + amt
        for tid in self._token_ids:
            if self._use_float:
                self._reserves[tid] = self._reserves[tid] * new_k / old_k
            else:
                self._reserves[tid] = self._reserves[tid] * new_k // old_k

        self._total_minted = new_k
        self._total_deposited += amt

        # Update LP position
        if agent_id in self._lp_positions:
            pos = self._lp_positions[agent_id]
            pos.deposited += amt
        else:
            self._lp_positions[agent_id] = LPPosition(
                agent_id=agent_id,
                position_id=position_id or str(agent_id),
                deposited=amt,
                share_fraction=0.0 if self._use_float else 0,
                accumulated_fees=0,
            )
        self._refresh_lp_shares()

        collateral_token = self._collateral_token
        return ExecutionResult(
            success=True,
            token_deltas={collateral_token: -amt},
            volume=0,
        )

    def withdraw_liquidity(
        self,
        agent_id: AgentId,
        amount: Numeric,
        position_id: str | None = None,
    ) -> ExecutionResult:
        """Withdraw collateral from the pool."""
        pos = self._lp_positions.get(agent_id)
        if pos is None:
            return ExecutionResult(success=False, error="no LP position found")

        amt = float(amount) if self._use_float else int(amount)
        if amt <= 0 or amt > pos.deposited:
            return ExecutionResult(success=False, error="invalid withdraw amount")

        old_k = self._total_minted
        new_k = old_k - amt
        if new_k <= 0:
            return ExecutionResult(success=False, error="cannot drain pool completely")

        for tid in self._token_ids:
            if self._use_float:
                self._reserves[tid] = self._reserves[tid] * new_k / old_k
            else:
                self._reserves[tid] = self._reserves[tid] * new_k // old_k

        self._total_minted = new_k
        self._total_deposited -= amt

        pos.deposited -= amt
        if pos.deposited <= 0:
            del self._lp_positions[agent_id]
        self._refresh_lp_shares()

        collateral_token = self._collateral_token
        return ExecutionResult(
            success=True,
            token_deltas={collateral_token: amt},
            volume=0,
        )

    def get_lp_state(self) -> LPState:
        return LPState(
            total_deposited=self._total_deposited,
            accumulated_fees=self._accumulated_fees,
            effective_liquidity=self._total_minted,
            num_lps=len(self._lp_positions),
        )

    def get_lp_position(self, agent_id: AgentId, position_id: str | None = None) -> LPPosition | None:
        return self._lp_positions.get(agent_id)

    def get_all_lp_positions(self) -> list[LPPosition]:
        return list(self._lp_positions.values())

    def reset_accumulated_fees(self) -> Numeric:
        fees = self._accumulated_fees
        self._accumulated_fees = 0.0 if self._use_float else 0
        return fees

    def rebalance_liquidity(
        self,
        agent_id: AgentId,
        target_weights: dict[TokenId, Numeric] | None,
    ) -> ExecutionResult:
        """Uniform-pool CFAMMs do not support per-LP reserve rebalancing."""
        if agent_id not in self._lp_positions:
            return ExecutionResult(success=False, error="no LP position found")
        if not target_weights:
            return ExecutionResult(success=False, error="target_weights are required for rebalance")
        return ExecutionResult(
            success=False,
            error="LP rebalance is not supported for uniform CFAMM pools",
        )

    # --- CFAMM-specific methods ---

    def _sqrt(self, value: Numeric) -> Numeric:
        return math.sqrt(value) if self._use_float else isqrt(int(value))

    def compute_buy(self, token: TokenId, collateral: Numeric) -> tuple[Numeric, Numeric]:
        """Buy tokens for a single asset. Mutates reserves in-place."""
        k = self._total_minted
        col = float(collateral) if self._use_float else int(collateral)

        x_i = k - self._reserves[token]
        sum_others_x_sq = 0
        for tid in self._token_ids:
            if tid != token:
                x_j = k - self._reserves[tid]
                sum_others_x_sq += x_j * x_j

        # Mint complete sets
        for tid in self._token_ids:
            self._reserves[tid] += col

        k_new = k + col
        k_new_sq = k_new * k_new

        x_new_i = self._sqrt(k_new_sq - sum_others_x_sq)
        tokens_out = x_new_i - x_i
        self._reserves[token] = k_new - x_new_i
        self._total_minted = k_new

        return tokens_out, k_new

    def compute_sell(self, token: TokenId, tokens_in: Numeric) -> tuple[Numeric, Numeric]:
        """Sell tokens for a single asset. Mutates reserves in-place."""
        k = self._total_minted
        t_in = float(tokens_in) if self._use_float else int(tokens_in)

        x_i = k - self._reserves[token]
        if x_i < t_in:
            raise ValueError("insufficient position to sell")

        self._reserves[token] += t_in

        k_new_sq = 0
        for tid in self._token_ids:
            x_j = k - self._reserves[tid]
            k_new_sq += x_j * x_j

        k_new = self._sqrt(k_new_sq)
        collateral_out = k - k_new

        for tid in self._token_ids:
            self._reserves[tid] -= collateral_out

        self._total_minted = k_new
        return collateral_out, k_new

    def compute_distribution_buy(
        self, weights: dict[TokenId, Numeric], collateral: Numeric
    ) -> tuple[dict[TokenId, Numeric], Numeric]:
        """Buy a weighted bundle. Mutates reserves in-place."""
        k = self._total_minted
        col = float(collateral) if self._use_float else int(collateral)

        xw = 0
        w2 = 0
        for tid in self._token_ids:
            x_b = k - self._reserves[tid]
            w_b = float(weights.get(tid, 0)) if self._use_float else int(weights.get(tid, 0))
            xw += x_b * w_b
            w2 += w_b * w_b

        if w2 == 0:
            raise ValueError("weights must be non-zero")

        for tid in self._token_ids:
            self._reserves[tid] += col

        k_new = k + col
        k_new_sq = k_new * k_new
        k_old_sq = k * k

        disc = xw * xw + w2 * (k_new_sq - k_old_sq)
        sqrt_disc = self._sqrt(disc)
        numerator = sqrt_disc - xw

        tokens_out: dict[TokenId, Numeric] = {}
        for tid in self._token_ids:
            w_b = float(weights.get(tid, 0)) if self._use_float else int(weights.get(tid, 0))
            if self._use_float:
                out = numerator * w_b / w2
            else:
                out = numerator * w_b // w2
            tokens_out[tid] = out
            self._reserves[tid] -= out

        self._total_minted = k_new
        return tokens_out, k_new

    def compute_distribution_sell(
        self, weights: dict[TokenId, Numeric], total_tokens: Numeric
    ) -> tuple[Numeric, Numeric]:
        """Sell a weighted bundle. Mutates reserves in-place."""
        k = self._total_minted
        t_total = float(total_tokens) if self._use_float else int(total_tokens)
        scale = 1.0 if self._use_float else (self._tokens[0].scale if self._tokens else 10**9)

        k_new_sq = 0
        for tid in self._token_ids:
            w_b = float(weights.get(tid, 0)) if self._use_float else int(weights.get(tid, 0))
            if self._use_float:
                tokens_for_bin = t_total * w_b / scale
            else:
                tokens_for_bin = t_total * w_b // scale
            self._reserves[tid] += tokens_for_bin
            x_new = k - self._reserves[tid]
            if x_new < 0:
                x_new = 0
            k_new_sq += x_new * x_new

        k_new = self._sqrt(k_new_sq)
        collateral_out = k - k_new

        for tid in self._token_ids:
            self._reserves[tid] -= collateral_out

        self._total_minted = k_new
        return collateral_out, k_new

    def verify_invariant(self, tolerance: int | None = None) -> bool:
        """Verify L2-norm invariant holds within tolerance.
        Default tolerance scales with total_minted to handle isqrt rounding."""
        k = self._total_minted
        actual = sum((k - r) ** 2 for r in self._reserves.values())
        expected = k * k
        if tolerance is None:
            # Scale tolerance: isqrt error is O(1), but squared terms amplify it
            # Use k * num_assets as a conservative bound
            tolerance = max(256, k * self._num_assets)
        return abs(actual - expected) <= tolerance

    # --- Internal helpers ---

    def _compute_invariant(self) -> Numeric:
        k = self._total_minted
        return sum((k - r) ** 2 for r in self._reserves.values())

    def _lp_share_scale(self) -> Numeric:
        if self._use_float:
            return 1.0
        token_scale = self._tokens[0].scale if self._tokens else 10**9
        return max(int(token_scale), 10**9)

    def _refresh_lp_shares(self) -> None:
        if not self._lp_positions:
            return

        total_pool_deposited = self._total_deposited
        if total_pool_deposited <= 0:
            for position in self._lp_positions.values():
                position.share_fraction = 0.0 if self._use_float else 0
            return

        positions = list(self._lp_positions.values())
        if self._use_float:
            total_pool_deposited = float(total_pool_deposited)
            for position in positions:
                position.share_fraction = float(position.deposited) / total_pool_deposited
            return

        scale = int(self._lp_share_scale())
        total_pool_deposited = int(total_pool_deposited)
        total_active_deposited = sum(int(position.deposited) for position in positions)
        total_active_share = total_active_deposited * scale // total_pool_deposited
        remaining = total_active_share
        for index, position in enumerate(positions):
            if index == len(positions) - 1:
                share = remaining
            else:
                share = int(position.deposited) * scale // total_pool_deposited
                remaining -= share
            position.share_fraction = share

    def _execute_single(self, action: SingleAssetAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute a single-asset buy or sell."""
        fee_model = self.get_fee_model(ctx.default_fee_model)
        amt = float(action.amount) if self._use_float else int(action.amount)

        if action.side == Side.BUY:
            # Apply fee to collateral input
            net_collateral = amt
            fee_paid = 0
            fee_splits: dict[str, Numeric] = {}
            if fee_model is not None:
                fee_result = fee_model(amt, ctx)
                net_collateral = fee_result.net_amount
                fee_paid = fee_result.total_fee
                fee_splits = dict(fee_result.splits)
                self._accumulated_fees += fee_splits.get("lp", fee_paid)

            # Check agent can afford
            collateral_balance = ctx.agent_state.balance(action.collateral)
            if collateral_balance < amt:
                return ExecutionResult(success=False, error="insufficient balance")

            try:
                tokens_out, _ = self.compute_buy(action.asset, net_collateral)
            except (ValueError, AssertionError) as e:
                return ExecutionResult(success=False, error=str(e))

            return ExecutionResult(
                success=True,
                token_deltas={
                    action.collateral: -amt,
                    action.asset: tokens_out,
                },
                fees_paid=fee_paid,
                fee_splits=fee_splits,
                fee_token=action.collateral,
                volume=amt,
            )
        else:  # SELL
            # Check agent has tokens to sell
            token_balance = ctx.agent_state.balance(action.asset)
            if token_balance < amt:
                return ExecutionResult(success=False, error="insufficient token balance")

            try:
                collateral_out, _ = self.compute_sell(action.asset, amt)
            except (ValueError, AssertionError) as e:
                return ExecutionResult(success=False, error=str(e))

            # Apply fee to collateral output
            fee_paid = 0
            net_collateral = collateral_out
            fee_splits: dict[str, Numeric] = {}
            if fee_model is not None:
                fee_result = fee_model(collateral_out, ctx)
                net_collateral = fee_result.net_amount
                fee_paid = fee_result.total_fee
                fee_splits = dict(fee_result.splits)
                self._accumulated_fees += fee_splits.get("lp", fee_paid)

            return ExecutionResult(
                success=True,
                token_deltas={
                    action.asset: -amt,
                    action.collateral: net_collateral,
                },
                fees_paid=fee_paid,
                fee_splits=fee_splits,
                fee_token=action.collateral,
                volume=amt,
            )

    def _execute_swap(self, action: SwapAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute a generic swap by routing through the single-asset CFAMM path."""
        token_in_is_asset = action.token_in in self._token_ids
        token_out_is_asset = action.token_out in self._token_ids

        if action.token_in == action.token_out:
            return ExecutionResult(success=False, error="token_in and token_out must differ")

        if token_out_is_asset and not token_in_is_asset:
            return self._execute_single(
                SingleAssetAction(
                    agent_id=action.agent_id,
                    compute_unit_limit=action.compute_unit_limit,
                    compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
                    asset=action.token_out,
                    collateral=action.token_in,
                    amount=action.amount_in,
                    side=Side.BUY,
                ),
                ctx,
            )

        if token_in_is_asset and not token_out_is_asset:
            return self._execute_single(
                SingleAssetAction(
                    agent_id=action.agent_id,
                    compute_unit_limit=action.compute_unit_limit,
                    compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
                    asset=action.token_in,
                    collateral=action.token_out,
                    amount=action.amount_in,
                    side=Side.SELL,
                ),
                ctx,
            )

        if not token_in_is_asset or not token_out_is_asset:
            return ExecutionResult(success=False, error="swap tokens are not tradable on this CFAMM")

        # Asset-to-asset swaps route through an implicit collateral leg.
        collateral_token = "COLLATERAL"
        if ctx.parameters is not None:
            collateral_token = ctx.parameters.get("collateral_token", self._collateral_token)
        else:
            collateral_token = self._collateral_token

        market_copy = self.copy()
        temp_state = AgentState(
            agent_id=ctx.agent_state.agent_id,
            role=ctx.agent_state.role,
            balances=dict(ctx.agent_state.balances),
            cumulative_volume=ctx.agent_state.cumulative_volume,
            realized_pnl=ctx.agent_state.realized_pnl,
        )
        temp_ctx = ExecutionContext(
            agent_state=temp_state,
            current_round=ctx.current_round,
            total_rounds=ctx.total_rounds,
            timestamp=ctx.timestamp,
            market_state=market_copy.get_state(),
            numeric_mode=ctx.numeric_mode,
            default_fee_model=ctx.default_fee_model,
            execution_cost=ctx.execution_cost,
            parameters=ctx.parameters,
        )

        sell_result = market_copy._execute_single(
            SingleAssetAction(
                agent_id=action.agent_id,
                compute_unit_limit=action.compute_unit_limit,
                compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
                asset=action.token_in,
                collateral=collateral_token,
                amount=action.amount_in,
                side=Side.SELL,
            ),
            temp_ctx,
        )
        if not sell_result.success:
            return sell_result
        for token, delta in sell_result.token_deltas.items():
            temp_state.balances[token] = temp_state.balances.get(token, 0) + delta
        temp_ctx.market_state = market_copy.get_state()

        intermediate_amount = sell_result.token_deltas.get(collateral_token, 0)
        buy_result = market_copy._execute_single(
            SingleAssetAction(
                agent_id=action.agent_id,
                compute_unit_limit=action.compute_unit_limit,
                compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
                asset=action.token_out,
                collateral=collateral_token,
                amount=intermediate_amount,
                side=Side.BUY,
            ),
            temp_ctx,
        )
        if not buy_result.success:
            return buy_result

        self._reserves = dict(market_copy._reserves)
        self._total_minted = market_copy._total_minted
        self._accumulated_fees = market_copy._accumulated_fees

        merged_deltas: dict[TokenId, Numeric] = {}
        merged_fee_splits: dict[str, Numeric] = {}
        for result in (sell_result, buy_result):
            for token, delta in result.token_deltas.items():
                merged_deltas[token] = merged_deltas.get(token, 0) + delta
            for split, amount in result.fee_splits.items():
                merged_fee_splits[split] = merged_fee_splits.get(split, 0) + amount

        if merged_deltas.get(collateral_token) == 0:
            merged_deltas.pop(collateral_token, None)

        return ExecutionResult(
            success=True,
            token_deltas=merged_deltas,
            fees_paid=sell_result.fees_paid + buy_result.fees_paid,
            fee_splits=merged_fee_splits,
            fee_token=collateral_token,
            volume=action.amount_in,
        )

    def _execute_bundle(self, action: BundleAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute a bundle (distribution) buy or sell."""
        fee_model = self.get_fee_model(ctx.default_fee_model)
        amt = float(action.amount) if self._use_float else int(action.amount)

        fee_paid = 0
        fee_splits: dict[str, Numeric] = {}

        if action.side == Side.BUY:
            net_collateral = amt
            if fee_model is not None:
                fee_result = fee_model(amt, ctx)
                net_collateral = fee_result.net_amount
                fee_paid = fee_result.total_fee
                fee_splits = dict(fee_result.splits)
                self._accumulated_fees += fee_splits.get("lp", fee_paid)

            collateral_balance = ctx.agent_state.balance(action.collateral)
            if collateral_balance < amt:
                return ExecutionResult(success=False, error="insufficient balance")

            try:
                tokens_out, _ = self.compute_distribution_buy(action.weights, net_collateral)
            except (ValueError, AssertionError) as e:
                return ExecutionResult(success=False, error=str(e))

            deltas: dict[TokenId, Numeric] = {action.collateral: -amt}
            for tid, out in tokens_out.items():
                deltas[tid] = deltas.get(tid, 0) + out

            return ExecutionResult(
                success=True,
                token_deltas=deltas,
                fees_paid=fee_paid,
                fee_splits=fee_splits,
                fee_token=action.collateral,
                volume=amt,
            )

        scale = 1.0 if self._use_float else (self._tokens[0].scale if self._tokens else 10**9)
        for tid in self._token_ids:
            weight = float(action.weights.get(tid, 0)) if self._use_float else int(action.weights.get(tid, 0))
            if self._use_float:
                tokens_for_bin = amt * weight / scale
            else:
                tokens_for_bin = amt * weight // scale
            if ctx.agent_state.balance(tid) < tokens_for_bin:
                return ExecutionResult(success=False, error=f"insufficient token balance for {tid}")

        try:
            collateral_out, _ = self.compute_distribution_sell(action.weights, amt)
        except (ValueError, AssertionError) as e:
            return ExecutionResult(success=False, error=str(e))

        net_collateral = collateral_out
        if fee_model is not None:
            fee_result = fee_model(collateral_out, ctx)
            net_collateral = fee_result.net_amount
            fee_paid = fee_result.total_fee
            fee_splits = dict(fee_result.splits)
            self._accumulated_fees += fee_splits.get("lp", fee_paid)

        deltas = {action.collateral: net_collateral}
        for tid in self._token_ids:
            weight = float(action.weights.get(tid, 0)) if self._use_float else int(action.weights.get(tid, 0))
            if self._use_float:
                tokens_for_bin = amt * weight / scale
            else:
                tokens_for_bin = amt * weight // scale
            if tokens_for_bin > 0:
                deltas[tid] = deltas.get(tid, 0) - tokens_for_bin

        return ExecutionResult(
            success=True,
            token_deltas=deltas,
            fees_paid=fee_paid,
            fee_splits=fee_splits,
            fee_token=action.collateral,
            volume=amt,
        )

    def _execute_lp(self, action: LPAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute LP deposit/withdraw."""
        if action.collateral != self._collateral_token:
            return ExecutionResult(
                success=False,
                error=f"LP collateral must be {self._collateral_token}",
            )
        if action.lp_type == LPActionType.DEPOSIT:
            collateral_balance = ctx.agent_state.balance(action.collateral)
            if collateral_balance < action.amount:
                return ExecutionResult(success=False, error="insufficient balance for LP deposit")
            result = self.deposit_liquidity(
                action.agent_id, action.amount,
                weights=action.target_weights,
                price_range=action.price_range,
                position_id=action.position_id,
            )
            return result
        elif action.lp_type == LPActionType.WITHDRAW:
            return self.withdraw_liquidity(
                action.agent_id, action.amount,
                position_id=action.position_id,
            )
        elif action.lp_type == LPActionType.REBALANCE:
            return self.rebalance_liquidity(action.agent_id, action.target_weights)
        return ExecutionResult(success=False, error=f"Unsupported LP action: {action.lp_type}")
