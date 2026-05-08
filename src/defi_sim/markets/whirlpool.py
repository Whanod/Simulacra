"""Real Orca Whirlpool CLMM market.

Hydrated from real on-chain Whirlpool / TickArray / SPL-vault accounts and
swaps via the math ported in :mod:`defi_sim.markets.whirlpool_math` (the
``programs/whirlpool/src/math/{tick_math,token_math,swap_math}.rs`` ladder
from orca-so/whirlpools).

Compared to the on-chain swap_manager.rs path this simplifies in two ways
that don't affect the swap-output observation our calibration tests target:

* Adaptive fees are not modelled. The pools we calibrate against (canonical
  SOL/USDC) use the static fee_rate. ``adaptive_fee_info`` would only matter
  for adaptive-fee pools and is `None` for the SOL/USDC fixture.
* Reward growth tracking is dropped. Reward emissions don't affect the swap
  observable (price, liquidity, tick) — only LP claim accounting does.

Everything else — protocol fees, fee-growth global accumulators, tick
crossings with `liquidity_net`, partial fills, exact-input/output — matches
the on-chain implementation bit-for-bit.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from defi_sim._compat import msgpack
from defi_sim.core.market import (
    ConcentratedLPPosition,
    LPPosition,
    LPState,
    LiquidityPool,
    Market,
    PricedMarket,
    register_market_type,
)
from defi_sim.core.types import (
    Action,
    AgentId,
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
from defi_sim.engine.scheduler import LockedAction
from defi_sim.markets.whirlpool_math import (
    MAX_SQRT_PRICE_X64,
    MIN_SQRT_PRICE_X64,
    PROTOCOL_FEE_DENOMINATOR,
    Q64,
    SwapStep,
    compute_swap_step,
    get_amount_delta_a,
    get_amount_delta_b,
    sqrt_price_from_tick_index,
    tick_index_from_sqrt_price,
)


U128_MASK = (1 << 128) - 1


__all__ = ["WhirlpoolMarket", "TickEntry", "TickArrayState", "WhirlpoolPoolState"]


TICK_ARRAY_SIZE = 88


@dataclass
class TickEntry:
    """One tick within a tick array. Mirrors the Rust ``Tick`` struct."""

    initialized: bool = False
    liquidity_net: int = 0
    liquidity_gross: int = 0
    fee_growth_outside_a: int = 0
    fee_growth_outside_b: int = 0


@dataclass
class TickArrayState:
    """Parsed Whirlpool ``TickArray`` account.

    Only carries fields the swap path reads. Reward accumulators are dropped
    (see module docstring).
    """

    pubkey: str
    start_tick_index: int
    ticks: list[TickEntry] = field(default_factory=list)

    def copy(self) -> "TickArrayState":
        return TickArrayState(
            pubkey=self.pubkey,
            start_tick_index=self.start_tick_index,
            ticks=[copy.copy(t) for t in self.ticks],
        )

    def offset(self, tick_index: int, tick_spacing: int) -> int:
        lhs = tick_index - self.start_tick_index
        rhs = tick_spacing
        d, r = divmod(lhs, rhs)
        # Python divmod already produces the floor-style result we want for
        # both positive and negative numerators. The Rust port shifts by -1
        # when r < 0; with Python's divmod, r is always in [0, rhs), so no
        # extra adjustment is needed.
        del r
        return d

    def get_tick(self, tick_index: int, tick_spacing: int) -> TickEntry:
        offset = self.offset(tick_index, tick_spacing)
        if offset < 0 or offset >= len(self.ticks):
            raise IndexError(
                f"tick {tick_index} out of array (start={self.start_tick_index})"
            )
        return self.ticks[offset]

    def get_next_init_tick_index(
        self, tick_index: int, tick_spacing: int, a_to_b: bool
    ) -> int | None:
        """Find the next initialized tick within this array.

        Mirrors ``FixedTickArray::get_next_init_tick_index``. For a-to-b
        searches the starting offset is inclusive (price moves left, current
        tick can host the next-init); for b-to-a it is exclusive.
        """
        offset = self.offset(tick_index, tick_spacing)
        if not a_to_b:
            offset += 1
        while 0 <= offset < TICK_ARRAY_SIZE:
            tick = self.ticks[offset]
            if tick.initialized:
                return offset * tick_spacing + self.start_tick_index
            offset = offset - 1 if a_to_b else offset + 1
        return None


@dataclass
class _WhirlpoolPosition:
    """Internal record for a per-agent concentrated-liquidity position.

    Mirrors the on-chain ``Position`` account fields the lighthouse demo
    needs: tick range, raw L, fee-growth-inside snapshots at last
    collection, and per-position book-keeping for the IL/fees metrics.
    """

    agent_id: AgentId
    position_id: str
    tick_lower: int
    tick_upper: int
    sqrt_price_lower_x64: int
    sqrt_price_upper_x64: int
    liquidity: int = 0
    fee_growth_inside_a_last_x64: int = 0
    fee_growth_inside_b_last_x64: int = 0
    accumulated_fees_a: int = 0
    accumulated_fees_b: int = 0
    deposited_a: int = 0
    deposited_b: int = 0
    sqrt_price_x64_at_mint: int = 0
    in_range_rounds: int = 0
    total_rounds: int = 0


@dataclass
class WhirlpoolPoolState:
    """Mutable swap-path state for one Whirlpool pool."""

    pubkey: str
    tick_spacing: int
    fee_rate: int
    protocol_fee_rate: int
    liquidity: int
    sqrt_price_x64: int
    tick_current_index: int
    fee_growth_global_a: int = 0
    fee_growth_global_b: int = 0
    protocol_fee_owed_a: int = 0
    protocol_fee_owed_b: int = 0
    token_mint_a: str = ""
    token_mint_b: str = ""
    token_vault_a_pubkey: str = ""
    token_vault_b_pubkey: str = ""
    token_vault_a_amount: int = 0
    token_vault_b_amount: int = 0
    token_decimals_a: int = 0
    token_decimals_b: int = 0


@register_market_type
class WhirlpoolMarket(Market, PricedMarket, LiquidityPool):
    """Real Orca Whirlpool CLMM market.

    Hydrated by :class:`defi_sim_solana.replay.whirlpool_hydrator.WhirlpoolStateHydrator`
    from a captured corpus slot, or constructed inline from raw state for
    unit tests. Swap execution is bit-identical to the on-chain
    ``swap_manager.rs`` for the static-fee path.

    The market exposes the canonical CFAMM-shaped surface (``execute``,
    ``copy``, ``to_bytes``, ``get_prices``, ``get_depth``) so existing
    agent code (noise, manipulator, jito_searcher, swap_noise) keeps working
    without modification — every action they emit is routed through the
    real CLMM swap path.
    """

    market_type: ClassVar[str] = "whirlpool"
    supports_lp_rebalance: ClassVar[bool] = False

    def __init__(
        self,
        pool: WhirlpoolPoolState,
        tick_arrays: list[TickArrayState],
        *,
        token_a: Token,
        token_b: Token,
        pool_account_id: str | None = None,
        fee_model: Any = None,
    ):
        self._pool = pool
        self._tick_arrays = sorted(tick_arrays, key=lambda ta: ta.start_tick_index)
        self._token_a = token_a
        self._token_b = token_b
        self._token_ids = [token_a.id, token_b.id]
        self._tokens = [token_a, token_b]
        self._num_assets = 2
        self._pool_account_id_override = pool_account_id
        self.fee_model = fee_model
        # Track LP volume telemetry consistent with CfammMarket. Whirlpool
        # liquidity provision is per-tick-range and we don't model adds /
        # removes here — Phase 3.1.2 follow-up will add that surface; for
        # now ``LPState`` reports the seeded global liquidity and zero
        # accumulated fees.
        self._accumulated_fees: Numeric = 0
        # Per-round CLMM telemetry: count of initialized-tick crossings
        # consumed by swaps this round, and LP-fee totals split by token
        # side (token A vs token B). Drained each round by the simulation
        # via ``pop_round_telemetry()``.
        self._round_tick_crossings: int = 0
        self._round_lp_fees_a: int = 0
        self._round_lp_fees_b: int = 0
        # Run-cumulative swap volume in raw token-B (quote) units. Includes
        # both swap directions: A→B contributes ``amount_out_b``,
        # B→A contributes ``amount_in_b``. Used by
        # ``_compute_derived_metrics`` to surface total volume across runs
        # for fee-tier comparisons.
        self._total_volume_b_raw: int = 0
        # Per-agent concentrated-liquidity positions, keyed by
        # ``(agent_id, position_id)``. Populated by ``deposit_liquidity``
        # and drained by ``withdraw_liquidity``.
        self._positions: dict[tuple[AgentId, str], _WhirlpoolPosition] = {}
        # Snapshot of total deposited L at construction time. Used as
        # the "chain baseline" floor for the Total LP Deposits chart
        # so simulation-driven LP activity (``agent_lp_liquidity``)
        # can be displayed as a delta on top of the hydrated state.
        # Computed *after* the tick arrays are wired up so fork-mode
        # initialization (which materializes ticks before constructing
        # the market wrapper) is captured.
        self._baseline_lp_liquidity: int = self._total_deposited_l()

    # --- Convenience accessors -----------------------------------------

    @property
    def pool(self) -> WhirlpoolPoolState:
        return self._pool

    @property
    def tick_arrays(self) -> list[TickArrayState]:
        return self._tick_arrays

    @property
    def token_a(self) -> Token:
        return self._token_a

    @property
    def token_b(self) -> Token:
        return self._token_b

    # --- Market ABC ----------------------------------------------------

    def get_state(self) -> AmmSnapshot:
        prices = self.get_prices()
        return AmmSnapshot(
            num_assets=2,
            tokens=list(self._token_ids),
            reserves={
                self._token_a.id: self._pool.token_vault_a_amount,
                self._token_b.id: self._pool.token_vault_b_amount,
            },
            prices=prices,
            total_liquidity=self._pool.liquidity,
            invariant=self._pool.sqrt_price_x64,
            fee_bps=self._pool.fee_rate / 100.0,
        )

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        if isinstance(action, SwapAction):
            return self._execute_swap_action(action, ctx)
        if isinstance(action, SingleAssetAction):
            return self._execute_single_asset(action, ctx)
        if isinstance(action, BundleAction):
            return ExecutionResult(
                success=False,
                error="Whirlpool does not support bundle (multi-asset weighted) swaps",
            )
        if isinstance(action, LPAction):
            return self._execute_lp(action, ctx)
        return ExecutionResult(
            success=False, error=f"Unsupported action type: {type(action).__name__}"
        )

    def _pool_account_id(self) -> str:
        if self._pool_account_id_override is not None:
            return self._pool_account_id_override
        return self._pool.pubkey or f"whirlpool:{id(self):x}:pool"

    def resolve_locks(self, action: Action, state: Any = None) -> LockedAction:
        pool = self._pool_account_id()
        vault_a = self._pool.token_vault_a_pubkey or f"{pool}:vault_a"
        vault_b = self._pool.token_vault_b_pubkey or f"{pool}:vault_b"
        if isinstance(action, (SwapAction, SingleAssetAction, BundleAction, LPAction)):
            return LockedAction(
                action=action,
                read_locks=frozenset(),
                write_locks=frozenset({pool, vault_a, vault_b}),
            )
        return LockedAction(action=action)

    def copy(self) -> "WhirlpoolMarket":
        new_pool = WhirlpoolPoolState(**self._pool.__dict__)
        new_arrays = [ta.copy() for ta in self._tick_arrays]
        clone = WhirlpoolMarket(
            pool=new_pool,
            tick_arrays=new_arrays,
            token_a=self._token_a,
            token_b=self._token_b,
            pool_account_id=self._pool_account_id_override,
            fee_model=self.fee_model,
        )
        clone._accumulated_fees = self._accumulated_fees
        clone._positions = {
            key: _WhirlpoolPosition(**pos.__dict__)
            for key, pos in self._positions.items()
        }
        # Mutable per-round / per-run telemetry must travel with the
        # clone. ``atomic_state_boundary`` snapshots the market via
        # ``copy()`` at slot start; on bundle rollback the engine
        # rebinds ``self._market`` to that snapshot. If we don't carry
        # these counters across, every rollback silently zeroes them.
        # The run-cumulative ``_total_volume_b_raw`` is the most visible
        # casualty (the ``total_volume_quote`` derived metric reads it
        # at end-of-run); the per-round counters are drained by
        # ``pop_round_telemetry`` so they normally bleed into the next
        # round if a rollback lands mid-round.
        clone._round_tick_crossings = self._round_tick_crossings
        clone._round_lp_fees_a = self._round_lp_fees_a
        clone._round_lp_fees_b = self._round_lp_fees_b
        clone._total_volume_b_raw = self._total_volume_b_raw
        # The baseline must carry across copies. Otherwise an
        # ``atomic_state_boundary`` snapshot taken mid-run rebinds with
        # ``baseline = current`` (because ``__init__`` recomputed it
        # from already-mutated tick gross), and ``agent_lp_liquidity``
        # silently re-zeroes after a rollback.
        clone._baseline_lp_liquidity = self._baseline_lp_liquidity
        return clone

    def to_bytes(self) -> bytes:
        payload = encode_msgpack_value(
            {
                "pool": self._pool.__dict__,
                "tick_arrays": [
                    {
                        "pubkey": ta.pubkey,
                        "start_tick_index": ta.start_tick_index,
                        "ticks": [t.__dict__ for t in ta.ticks],
                    }
                    for ta in self._tick_arrays
                ],
                "token_a": (self._token_a.id, self._token_a.symbol, self._token_a.decimals),
                "token_b": (self._token_b.id, self._token_b.symbol, self._token_b.decimals),
                "pool_account_id_override": self._pool_account_id_override,
                "accumulated_fees": self._accumulated_fees,
                "positions": [
                    {
                        "agent_id": pos.agent_id,
                        "position_id": pos.position_id,
                        "tick_lower": pos.tick_lower,
                        "tick_upper": pos.tick_upper,
                        "sqrt_price_lower_x64": pos.sqrt_price_lower_x64,
                        "sqrt_price_upper_x64": pos.sqrt_price_upper_x64,
                        "liquidity": pos.liquidity,
                        "fee_growth_inside_a_last_x64": pos.fee_growth_inside_a_last_x64,
                        "fee_growth_inside_b_last_x64": pos.fee_growth_inside_b_last_x64,
                        "accumulated_fees_a": pos.accumulated_fees_a,
                        "accumulated_fees_b": pos.accumulated_fees_b,
                        "deposited_a": pos.deposited_a,
                        "deposited_b": pos.deposited_b,
                        "sqrt_price_x64_at_mint": pos.sqrt_price_x64_at_mint,
                        "in_range_rounds": pos.in_range_rounds,
                        "total_rounds": pos.total_rounds,
                    }
                    for pos in self._positions.values()
                ],
            }
        )
        return msgpack.packb(payload, use_bin_type=True)

    @classmethod
    def from_bytes(cls, data: bytes) -> "WhirlpoolMarket":
        d = decode_msgpack_value(msgpack.unpackb(data, raw=False, strict_map_key=False))
        token_a = Token(id=d["token_a"][0], symbol=d["token_a"][1], decimals=d["token_a"][2])
        token_b = Token(id=d["token_b"][0], symbol=d["token_b"][1], decimals=d["token_b"][2])
        pool = WhirlpoolPoolState(**d["pool"])
        arrays = [
            TickArrayState(
                pubkey=ta["pubkey"],
                start_tick_index=ta["start_tick_index"],
                ticks=[TickEntry(**t) for t in ta["ticks"]],
            )
            for ta in d["tick_arrays"]
        ]
        market = cls(
            pool=pool,
            tick_arrays=arrays,
            token_a=token_a,
            token_b=token_b,
            pool_account_id=d.get("pool_account_id_override"),
        )
        market._accumulated_fees = d.get("accumulated_fees", 0)
        for raw in d.get("positions", []) or []:
            pos = _WhirlpoolPosition(
                agent_id=raw["agent_id"],
                position_id=raw["position_id"],
                tick_lower=int(raw["tick_lower"]),
                tick_upper=int(raw["tick_upper"]),
                sqrt_price_lower_x64=int(raw["sqrt_price_lower_x64"]),
                sqrt_price_upper_x64=int(raw["sqrt_price_upper_x64"]),
                liquidity=int(raw["liquidity"]),
                fee_growth_inside_a_last_x64=int(raw["fee_growth_inside_a_last_x64"]),
                fee_growth_inside_b_last_x64=int(raw["fee_growth_inside_b_last_x64"]),
                accumulated_fees_a=int(raw["accumulated_fees_a"]),
                accumulated_fees_b=int(raw["accumulated_fees_b"]),
                deposited_a=int(raw["deposited_a"]),
                deposited_b=int(raw["deposited_b"]),
                sqrt_price_x64_at_mint=int(raw["sqrt_price_x64_at_mint"]),
                in_range_rounds=int(raw.get("in_range_rounds", 0)),
                total_rounds=int(raw.get("total_rounds", 0)),
            )
            market._positions[(pos.agent_id, pos.position_id)] = pos
        return market

    def configure_numeric_mode(self, numeric_mode: NumericMode) -> None:
        # Whirlpool math is integer-exact by definition. Float-mode runs
        # still use integer accumulators for the CLMM state itself; the
        # numeric_mode toggle is honored by other markets that have a
        # genuine choice between fixed-point and float.
        return None

    # --- PricedMarket --------------------------------------------------

    def get_prices(self) -> dict[TokenId, Numeric]:
        """Spot price implied by ``sqrt_price_x64``.

        The returned mapping is ``{token_a.id: price_in_b_per_a, token_b.id: 1}``
        in **decimal-adjusted** units, mirroring what humans expect for a
        SOL/USDC pool — ``prices['SOL']`` reads as USDC per SOL.
        """
        sqrt_price = self._pool.sqrt_price_x64
        # Q64.64 squared -> Q128.128, so divide by 2**128 to recover float.
        # Use the decimal-adjusted formula to keep the surface human-readable.
        ratio_raw = (sqrt_price * sqrt_price) / (1 << 128)
        decimals_diff = self._token_a.decimals - self._token_b.decimals
        scale_factor = 10 ** decimals_diff if decimals_diff >= 0 else 1.0 / (
            10 ** abs(decimals_diff)
        )
        price_b_per_a = ratio_raw * scale_factor
        return {self._token_a.id: price_b_per_a, self._token_b.id: 1.0}

    def get_depth(self, token: TokenId) -> Numeric:
        if token == self._token_a.id:
            return self._pool.token_vault_a_amount
        if token == self._token_b.id:
            return self._pool.token_vault_b_amount
        return 0

    def quote_pnl(self, deltas: dict[TokenId, Numeric]) -> Numeric:
        """Mark ``deltas`` to ``token_b`` base units at the current spot.

        Used by the engine to attribute realized PnL on each fill. The
        returned value is in raw ``token_b`` units (matching the
        ``cumulative_volume`` / ``balances`` convention) so it survives
        integer-mode coercion. ``token_b`` is treated as the quote leg
        (USDC for SOL/USDC), which matches the Whirlpool convention
        ``prices[token_a] = b_per_a, prices[token_b] = 1``.
        """
        sqrt_p = self._pool.sqrt_price_x64
        if sqrt_p == 0:
            return 0
        delta_a = int(deltas.get(self._token_a.id, 0))
        delta_b = int(deltas.get(self._token_b.id, 0))
        # raw_b_per_raw_a = sqrt_price_x64^2 / 2^128. Multiply first to
        # avoid losing the fractional ratio in integer division.
        a_in_b = (delta_a * sqrt_p * sqrt_p) // (1 << 128) if delta_a >= 0 else -(
            (-delta_a * sqrt_p * sqrt_p) // (1 << 128)
        )
        return delta_b + a_in_b

    # --- LiquidityPool: concentrated-liquidity deposit / withdraw -------

    def deposit_liquidity(
        self,
        agent_id: AgentId,
        amount: Numeric,
        weights: dict[TokenId, Numeric] | None = None,
        price_range: tuple[Numeric, Numeric] | None = None,
        position_id: str | None = None,
    ) -> ExecutionResult:
        """Mint a concentrated-liquidity position.

        ``amount`` is interpreted as the LP's token-B (quote) budget cap
        in raw base units; the matching token-A side is consumed up to
        the agent's available balance, with L scaled down to the smaller
        side. ``price_range`` is in human/decimal-adjusted units (the
        same scale ``get_prices()`` returns); pass ``None`` for a
        full-range mint.
        """
        amount_b_budget = int(amount)
        if amount_b_budget <= 0:
            return ExecutionResult(success=False, error="deposit amount must be positive")

        sqrt_lower, sqrt_upper, tick_lower, tick_upper = self._resolve_range(price_range)
        if sqrt_upper <= sqrt_lower:
            return ExecutionResult(
                success=False, error="deposit range must be non-empty (lower < upper)"
            )

        sqrt_current = self._pool.sqrt_price_x64
        if weights is not None and self._token_a.id in weights:
            amount_a_budget = int(weights[self._token_a.id])
            if self._token_b.id in weights:
                amount_b_budget = min(amount_b_budget, int(weights[self._token_b.id]))
        else:
            amount_a_budget = self._token_a_budget_for(amount_b_budget, sqrt_current)

        liquidity = self._compute_liquidity_from_budgets(
            sqrt_current,
            sqrt_lower,
            sqrt_upper,
            amount_a_budget,
            amount_b_budget,
        )
        if liquidity <= 0:
            return ExecutionResult(
                success=False,
                error="computed liquidity is zero — budgets too small for range",
            )

        delta_a, delta_b = self._amounts_for_liquidity(
            sqrt_current, sqrt_lower, sqrt_upper, liquidity, round_up=True
        )

        # Update boundary ticks and (if in range) active liquidity.
        self._update_tick(tick_lower, liquidity, is_upper_boundary=False)
        self._update_tick(tick_upper, liquidity, is_upper_boundary=True)
        if tick_lower <= self._pool.tick_current_index < tick_upper:
            self._pool.liquidity += liquidity

        # Snapshot fee_growth_inside at mint so the next withdraw can
        # diff it for collected-fee accounting.
        fg_inside_a, fg_inside_b = self._fee_growth_inside(tick_lower, tick_upper)

        pid = position_id or str(agent_id)
        key = (agent_id, pid)
        existing = self._positions.get(key)
        if existing is not None:
            # Add to existing position. Collect any pending fees first,
            # then mint additional L on top.
            self._collect_fees_into_position(existing)
            existing.liquidity += liquidity
            existing.deposited_a += delta_a
            existing.deposited_b += delta_b
            existing.fee_growth_inside_a_last_x64 = fg_inside_a
            existing.fee_growth_inside_b_last_x64 = fg_inside_b
        else:
            self._positions[key] = _WhirlpoolPosition(
                agent_id=agent_id,
                position_id=pid,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                sqrt_price_lower_x64=sqrt_lower,
                sqrt_price_upper_x64=sqrt_upper,
                liquidity=liquidity,
                fee_growth_inside_a_last_x64=fg_inside_a,
                fee_growth_inside_b_last_x64=fg_inside_b,
                deposited_a=delta_a,
                deposited_b=delta_b,
                sqrt_price_x64_at_mint=sqrt_current,
            )

        # Vault accounting: pool gains the deposited tokens; agent loses
        # them via token_deltas.
        self._pool.token_vault_a_amount += delta_a
        self._pool.token_vault_b_amount += delta_b

        return ExecutionResult(
            success=True,
            token_deltas={
                self._token_a.id: -delta_a,
                self._token_b.id: -delta_b,
            },
        )

    def withdraw_liquidity(
        self,
        agent_id: AgentId,
        amount: Numeric,
        position_id: str | None = None,
    ) -> ExecutionResult:
        """Burn a concentrated-liquidity position and collect fees.

        ``amount`` is ignored — withdraws are full-position by
        ``position_id`` (defaults to ``agent_id``). Returns the LP's
        share of the underlying token amounts plus any accrued fees.
        """
        pid = position_id or str(agent_id)
        key = (agent_id, pid)
        position = self._positions.get(key)
        if position is None:
            return ExecutionResult(success=False, error="no LP position found")

        # Collect any outstanding fees into the position first.
        self._collect_fees_into_position(position)

        sqrt_current = self._pool.sqrt_price_x64
        delta_a, delta_b = self._amounts_for_liquidity(
            sqrt_current,
            position.sqrt_price_lower_x64,
            position.sqrt_price_upper_x64,
            position.liquidity,
            round_up=False,
        )

        # Pull L back from boundary ticks and active liquidity if in
        # range.
        self._update_tick(position.tick_lower, -position.liquidity, is_upper_boundary=False)
        self._update_tick(position.tick_upper, -position.liquidity, is_upper_boundary=True)
        if position.tick_lower <= self._pool.tick_current_index < position.tick_upper:
            self._pool.liquidity = max(0, self._pool.liquidity - position.liquidity)

        # Cap by what's actually in the vault — defensive against
        # rounding drift.
        delta_a = min(delta_a, max(0, self._pool.token_vault_a_amount))
        delta_b = min(delta_b, max(0, self._pool.token_vault_b_amount))

        fees_a = position.accumulated_fees_a
        fees_b = position.accumulated_fees_b

        return_a = delta_a + fees_a
        return_b = delta_b + fees_b

        # Cap fee withdrawal against vault as well; LP fee accounting is
        # against ``_accumulated_fees`` which is denominated as the swap
        # path tracks it (see ``_build_result``).
        return_a = min(return_a, max(0, self._pool.token_vault_a_amount))
        return_b = min(return_b, max(0, self._pool.token_vault_b_amount))

        self._pool.token_vault_a_amount -= return_a
        self._pool.token_vault_b_amount -= return_b

        # Drain the position.
        del self._positions[key]

        return ExecutionResult(
            success=True,
            token_deltas={
                self._token_a.id: return_a,
                self._token_b.id: return_b,
            },
            fee_splits={"lp_fees_a": int(fees_a), "lp_fees_b": int(fees_b)},
        )

    def get_lp_state(self) -> LPState:
        return LPState(
            total_deposited=int(self._pool.liquidity),
            accumulated_fees=self._accumulated_fees,
            effective_liquidity=self._pool.liquidity,
            num_lps=len(self._positions),
        )

    def get_lp_position(
        self, agent_id: AgentId, position_id: str | None = None
    ) -> LPPosition | None:
        pid = position_id or str(agent_id)
        position = self._positions.get((agent_id, pid))
        if position is None:
            return None
        return self._to_concentrated_position(position)

    def get_all_lp_positions(self) -> list[LPPosition]:
        return [self._to_concentrated_position(p) for p in self._positions.values()]

    def reset_accumulated_fees(self) -> Numeric:
        fees = self._accumulated_fees
        self._accumulated_fees = 0
        return fees

    def _total_deposited_l(self) -> int:
        """Sum of ``L`` across every minted position in this pool.

        Derived from boundary tick state rather than ``self._positions``:
        each mint adds ``L`` to *both* its lower and upper tick's
        ``liquidity_gross``, so summing gross across all initialized
        ticks and halving recovers total deposited ``L``. This works
        uniformly for runtime-minted positions *and* positions
        hydrated from chain state in fork mode (``self._positions``
        only tracks runtime mints, so a sum over it returns zero for
        forked pools even when there's real chain liquidity).

        Note: if a single mint's lower or upper tick falls outside the
        loaded tick arrays for some reason, this can undercount —
        ``_update_tick`` always materializes the array via
        ``_ensure_tick_array`` so that's not a concern in practice.
        """
        total_gross = 0
        for ta in self._tick_arrays:
            for tick in ta.ticks:
                if tick.initialized:
                    total_gross += int(tick.liquidity_gross)
        return total_gross // 2

    def pop_round_telemetry(self) -> dict[str, int]:
        """Drain per-round CLMM counters and return a snapshot dict.

        Returns:
            ``tick_crossings`` — initialized-tick crossings consumed by
              swaps this round (proxy for "did liquidity get churned through").
            ``lp_fees_a`` / ``lp_fees_b`` — LP-fee revenue split by token side.
            ``active_liquidity`` — current ``L`` at end of round (the L the
              price is sitting on, distinct from total deposited reserves).
            ``total_lp_liquidity`` — sum of ``L`` across all minted
              positions, regardless of in-range status. Stable under
              price drift; only steps on mint / burn events. Computed
              from boundary tick gross so it covers chain-hydrated
              fork state as well as runtime mints.
            ``baseline_lp_liquidity`` — snapshot of total deposited L
              taken when the market was constructed (chain-hydrated
              positions for fork runs, zero for synthetic pools). The
              chart layer renders this as the floor under
              ``agent_lp_liquidity`` so chain state and simulation
              activity stack visually.
            ``agent_lp_liquidity`` — net L added (or removed) by
              simulated agents during the run, ``total - baseline``.
              Starts at 0 and steps every time an LP agent mints or
              burns. Negative if agents have on-net withdrawn below
              the chain baseline — clamped to ``-baseline`` since
              total cannot go below 0.
            ``current_tick_index`` — for orientation against the tick map.
        """
        total_lp = self._total_deposited_l()
        agent_lp = total_lp - self._baseline_lp_liquidity
        payload = {
            "tick_crossings": self._round_tick_crossings,
            "lp_fees_a": self._round_lp_fees_a,
            "lp_fees_b": self._round_lp_fees_b,
            "active_liquidity": int(self._pool.liquidity),
            "total_lp_liquidity": total_lp,
            "baseline_lp_liquidity": int(self._baseline_lp_liquidity),
            "agent_lp_liquidity": agent_lp,
            "current_tick_index": int(self._pool.tick_current_index),
        }
        self._round_tick_crossings = 0
        self._round_lp_fees_a = 0
        self._round_lp_fees_b = 0
        return payload

    def tick_lp_round_stats(self) -> None:
        """Increment per-position in-range counters for the current round.

        Called once per round by the engine after the round's swaps have
        settled, so the resulting counts reflect the LP's experience at
        end-of-round (when ``tick_current_index`` is final).
        """
        for pos in self._positions.values():
            pos.total_rounds += 1
            if pos.tick_lower <= self._pool.tick_current_index < pos.tick_upper:
                pos.in_range_rounds += 1

    # --- LP execution helpers ------------------------------------------

    def _execute_lp(self, action: LPAction, ctx: ExecutionContext) -> ExecutionResult:
        if action.lp_type == LPActionType.DEPOSIT:
            amount_b_budget = int(action.amount)
            if amount_b_budget <= 0:
                return ExecutionResult(success=False, error="deposit amount must be positive")
            balance_b = ctx.agent_state.balance(self._token_b.id)
            if balance_b < amount_b_budget:
                return ExecutionResult(
                    success=False,
                    error=f"insufficient {self._token_b.id} balance for LP deposit",
                )
            balance_a = int(ctx.agent_state.balance(self._token_a.id))
            return self.deposit_liquidity(
                action.agent_id,
                amount_b_budget,
                weights={
                    self._token_a.id: balance_a,
                    self._token_b.id: amount_b_budget,
                },
                price_range=action.price_range,
                position_id=action.position_id,
            )
        if action.lp_type == LPActionType.WITHDRAW:
            return self.withdraw_liquidity(
                action.agent_id,
                action.amount,
                position_id=action.position_id,
            )
        if action.lp_type == LPActionType.REBALANCE:
            return ExecutionResult(
                success=False, error="Whirlpool does not support LP rebalance"
            )
        return ExecutionResult(success=False, error=f"Unsupported LP action: {action.lp_type}")

    def _token_a_budget_for(self, amount_b_budget: int, sqrt_current: int) -> int:
        """Fallback when the caller didn't pre-supply a token-A cap.

        Estimates a generous token-A budget from the token-B amount at
        the current spot, so a balanced in-range deposit isn't bottle-
        necked when ``deposit_liquidity`` is called without ``weights``.
        """
        if sqrt_current <= 0:
            return amount_b_budget
        # raw_a_per_raw_b = 2^128 / sqrt_x64^2
        # amount_a ≈ amount_b * 2^128 / sqrt_current^2
        return (amount_b_budget * (1 << 128)) // (sqrt_current * sqrt_current) * 2

    def _resolve_range(
        self, price_range: tuple[Numeric, Numeric] | None
    ) -> tuple[int, int, int, int]:
        spacing = self._pool.tick_spacing
        if price_range is None:
            from defi_sim.markets.whirlpool_math import MAX_TICK_INDEX, MIN_TICK_INDEX
            tick_lower = (MIN_TICK_INDEX // spacing) * spacing
            tick_upper = (MAX_TICK_INDEX // spacing) * spacing
            sqrt_lower = sqrt_price_from_tick_index(tick_lower)
            sqrt_upper = sqrt_price_from_tick_index(tick_upper)
            return sqrt_lower, sqrt_upper, tick_lower, tick_upper

        p_lower, p_upper = price_range
        if float(p_lower) > float(p_upper):
            p_lower, p_upper = p_upper, p_lower

        sqrt_lower_raw = self._human_price_to_sqrt_x64(float(p_lower))
        sqrt_upper_raw = self._human_price_to_sqrt_x64(float(p_upper))
        sqrt_lower_raw = max(sqrt_lower_raw, MIN_SQRT_PRICE_X64)
        sqrt_upper_raw = min(sqrt_upper_raw, MAX_SQRT_PRICE_X64)

        tick_lower_raw = tick_index_from_sqrt_price(sqrt_lower_raw)
        tick_upper_raw = tick_index_from_sqrt_price(sqrt_upper_raw)

        tick_lower = (tick_lower_raw // spacing) * spacing
        if tick_upper_raw % spacing == 0:
            tick_upper = tick_upper_raw
        else:
            tick_upper = ((tick_upper_raw // spacing) + 1) * spacing
        if tick_upper <= tick_lower:
            tick_upper = tick_lower + spacing

        sqrt_lower = sqrt_price_from_tick_index(tick_lower)
        sqrt_upper = sqrt_price_from_tick_index(tick_upper)
        return sqrt_lower, sqrt_upper, tick_lower, tick_upper

    def _human_price_to_sqrt_x64(self, price_b_per_a: float) -> int:
        if price_b_per_a <= 0:
            raise ValueError("price must be positive")
        decimals_diff = self._token_a.decimals - self._token_b.decimals
        if decimals_diff >= 0:
            ratio_raw = price_b_per_a / (10 ** decimals_diff)
        else:
            ratio_raw = price_b_per_a * (10 ** (-decimals_diff))
        return int(math.sqrt(ratio_raw) * (1 << 64))

    @staticmethod
    def _compute_liquidity_from_budgets(
        sqrt_curr: int,
        sqrt_lower: int,
        sqrt_upper: int,
        amount_a: int,
        amount_b: int,
    ) -> int:
        """Maximum L producible from ``(amount_a, amount_b)`` at spot."""
        if sqrt_curr <= sqrt_lower:
            if amount_a <= 0:
                return 0
            return (amount_a * sqrt_lower * sqrt_upper) // ((sqrt_upper - sqrt_lower) << 64)
        if sqrt_curr >= sqrt_upper:
            if amount_b <= 0:
                return 0
            return (amount_b << 64) // (sqrt_upper - sqrt_lower)
        # In-range: bottlenecked by the smaller side.
        if amount_a <= 0 or amount_b <= 0:
            return 0
        l_from_a = (amount_a * sqrt_curr * sqrt_upper) // ((sqrt_upper - sqrt_curr) << 64)
        l_from_b = (amount_b << 64) // (sqrt_curr - sqrt_lower)
        return min(l_from_a, l_from_b)

    @staticmethod
    def _amounts_for_liquidity(
        sqrt_curr: int,
        sqrt_lower: int,
        sqrt_upper: int,
        liquidity: int,
        round_up: bool,
    ) -> tuple[int, int]:
        if liquidity <= 0:
            return 0, 0
        if sqrt_curr <= sqrt_lower:
            return get_amount_delta_a(sqrt_lower, sqrt_upper, liquidity, round_up), 0
        if sqrt_curr >= sqrt_upper:
            return 0, get_amount_delta_b(sqrt_lower, sqrt_upper, liquidity, round_up)
        delta_a = get_amount_delta_a(sqrt_curr, sqrt_upper, liquidity, round_up)
        delta_b = get_amount_delta_b(sqrt_lower, sqrt_curr, liquidity, round_up)
        return delta_a, delta_b

    def _ensure_tick_array(self, tick_index: int) -> TickArrayState:
        spacing = self._pool.tick_spacing
        span = TICK_ARRAY_SIZE * spacing
        # Floor-divide so negative indexes still align onto the
        # correct array boundary.
        start = (tick_index // span) * span
        for ta in self._tick_arrays:
            if ta.start_tick_index == start:
                return ta
        ta = TickArrayState(
            pubkey=f"{self._pool_account_id()}:dyn_array:{start}",
            start_tick_index=start,
            ticks=[TickEntry() for _ in range(TICK_ARRAY_SIZE)],
        )
        self._tick_arrays.append(ta)
        self._tick_arrays.sort(key=lambda t: t.start_tick_index)
        return ta

    def _update_tick(
        self, tick_index: int, liquidity_delta: int, *, is_upper_boundary: bool
    ) -> None:
        if liquidity_delta == 0:
            return
        array = self._ensure_tick_array(tick_index)
        spacing = self._pool.tick_spacing
        offset = array.offset(tick_index, spacing)
        tick = array.ticks[offset]

        if not tick.initialized and liquidity_delta > 0:
            # First-time init: V3 convention seeds fee_growth_outside.
            if tick_index <= self._pool.tick_current_index:
                tick.fee_growth_outside_a = self._pool.fee_growth_global_a
                tick.fee_growth_outside_b = self._pool.fee_growth_global_b
            tick.initialized = True

        new_gross = tick.liquidity_gross + liquidity_delta
        if new_gross < 0:
            new_gross = 0
        tick.liquidity_gross = new_gross

        if is_upper_boundary:
            tick.liquidity_net = tick.liquidity_net - liquidity_delta
        else:
            tick.liquidity_net = tick.liquidity_net + liquidity_delta

        if new_gross == 0:
            tick.initialized = False

    def _fee_growth_inside(self, tick_lower: int, tick_upper: int) -> tuple[int, int]:
        tick_current = self._pool.tick_current_index
        fg_global_a = self._pool.fee_growth_global_a
        fg_global_b = self._pool.fee_growth_global_b

        lower_entry = self._get_tick(tick_lower)
        upper_entry = self._get_tick(tick_upper)
        fg_outside_lower_a = lower_entry.fee_growth_outside_a if lower_entry else 0
        fg_outside_lower_b = lower_entry.fee_growth_outside_b if lower_entry else 0
        fg_outside_upper_a = upper_entry.fee_growth_outside_a if upper_entry else 0
        fg_outside_upper_b = upper_entry.fee_growth_outside_b if upper_entry else 0

        if tick_current >= tick_lower:
            fg_below_a = fg_outside_lower_a
            fg_below_b = fg_outside_lower_b
        else:
            fg_below_a = (fg_global_a - fg_outside_lower_a) & U128_MASK
            fg_below_b = (fg_global_b - fg_outside_lower_b) & U128_MASK

        if tick_current < tick_upper:
            fg_above_a = fg_outside_upper_a
            fg_above_b = fg_outside_upper_b
        else:
            fg_above_a = (fg_global_a - fg_outside_upper_a) & U128_MASK
            fg_above_b = (fg_global_b - fg_outside_upper_b) & U128_MASK

        fg_inside_a = (fg_global_a - fg_below_a - fg_above_a) & U128_MASK
        fg_inside_b = (fg_global_b - fg_below_b - fg_above_b) & U128_MASK
        return fg_inside_a, fg_inside_b

    def _collect_fees_into_position(self, position: _WhirlpoolPosition) -> None:
        fg_inside_a, fg_inside_b = self._fee_growth_inside(
            position.tick_lower, position.tick_upper
        )
        delta_a = (fg_inside_a - position.fee_growth_inside_a_last_x64) & U128_MASK
        delta_b = (fg_inside_b - position.fee_growth_inside_b_last_x64) & U128_MASK
        if position.liquidity > 0:
            position.accumulated_fees_a += (delta_a * position.liquidity) >> 64
            position.accumulated_fees_b += (delta_b * position.liquidity) >> 64
        position.fee_growth_inside_a_last_x64 = fg_inside_a
        position.fee_growth_inside_b_last_x64 = fg_inside_b

    def _to_concentrated_position(
        self, position: _WhirlpoolPosition
    ) -> ConcentratedLPPosition:
        in_range = (
            position.tick_lower <= self._pool.tick_current_index < position.tick_upper
        )
        return ConcentratedLPPosition(
            agent_id=position.agent_id,
            position_id=position.position_id,
            deposited=int(position.liquidity),
            share_fraction=0,
            accumulated_fees=int(
                position.accumulated_fees_a + position.accumulated_fees_b
            ),
            tick_lower=position.tick_lower,
            tick_upper=position.tick_upper,
            liquidity=int(position.liquidity),
            in_range=in_range,
        )

    def position_record(
        self, agent_id: AgentId, position_id: str | None = None
    ) -> _WhirlpoolPosition | None:
        """Internal-record accessor used by metrics for IL bookkeeping."""
        pid = position_id or str(agent_id)
        return self._positions.get((agent_id, pid))

    def all_position_records(self) -> list[_WhirlpoolPosition]:
        return list(self._positions.values())

    def position_value_in_b(self, position: _WhirlpoolPosition) -> int:
        """Mark the position to ``token_b`` raw units at the current spot.

        Returns ``deposited_b + deposited_a × spot`` if the LP's range
        currently covers the spot, otherwise the all-token-A or all-token-B
        terminal value at the active boundary. Used by the range-IL metric.
        """
        sqrt_curr = self._pool.sqrt_price_x64
        delta_a, delta_b = self._amounts_for_liquidity(
            sqrt_curr,
            position.sqrt_price_lower_x64,
            position.sqrt_price_upper_x64,
            position.liquidity,
            round_up=False,
        )
        # delta_a × (sqrt_curr/2^64)^2 = a_in_b in raw units.
        a_in_b = (delta_a * sqrt_curr * sqrt_curr) // (1 << 128)
        return int(delta_b + a_in_b)

    def hodl_value_in_b(self, position: _WhirlpoolPosition) -> int:
        """Mark the position's *initial* deposit to token-B at current spot."""
        sqrt_curr = self._pool.sqrt_price_x64
        a_in_b = (position.deposited_a * sqrt_curr * sqrt_curr) // (1 << 128)
        return int(position.deposited_b + a_in_b)

    # --- Swap pipeline -------------------------------------------------

    def simulate_swap(
        self,
        amount: int,
        *,
        a_to_b: bool,
        amount_specified_is_input: bool = True,
        sqrt_price_limit: int | None = None,
    ) -> dict[str, int]:
        """Pure simulator — no state mutation.

        Returns a dict of post-swap fields so calibration tests can assert
        every observable: ``amount_a, amount_b, lp_fee, protocol_fee_a,
        protocol_fee_b, next_liquidity, next_tick_index, next_sqrt_price,
        fee_growth_global_a, fee_growth_global_b``.
        """
        snapshot = self.copy()
        return snapshot._swap_inplace(
            amount,
            a_to_b=a_to_b,
            amount_specified_is_input=amount_specified_is_input,
            sqrt_price_limit=sqrt_price_limit,
        )

    def _swap_inplace(
        self,
        amount: int,
        *,
        a_to_b: bool,
        amount_specified_is_input: bool,
        sqrt_price_limit: int | None,
    ) -> dict[str, int]:
        if amount == 0:
            raise ValueError("swap amount must be non-zero")
        if amount < 0:
            raise ValueError("swap amount must be non-negative")
        pool = self._pool

        if sqrt_price_limit is None:
            limit = MIN_SQRT_PRICE_X64 if a_to_b else MAX_SQRT_PRICE_X64
        else:
            limit = sqrt_price_limit
        if not (MIN_SQRT_PRICE_X64 <= limit <= MAX_SQRT_PRICE_X64):
            raise ValueError("sqrt_price_limit out of bounds")
        if a_to_b and limit >= pool.sqrt_price_x64:
            raise ValueError("a_to_b swap requires sqrt_price_limit < current")
        if not a_to_b and limit <= pool.sqrt_price_x64:
            raise ValueError("b_to_a swap requires sqrt_price_limit > current")

        amount_remaining = amount
        amount_calculated = 0
        curr_sqrt_price = pool.sqrt_price_x64
        curr_tick = pool.tick_current_index
        curr_liquidity = pool.liquidity
        curr_protocol_fee = 0
        fee_growth_input = (
            pool.fee_growth_global_a if a_to_b else pool.fee_growth_global_b
        )
        fee_sum = 0
        tick_crossings = 0

        while amount_remaining > 0 and curr_sqrt_price != limit:
            next_init_tick = self._next_initialized_tick(curr_tick, a_to_b)
            next_tick_sqrt = (
                sqrt_price_from_tick_index(next_init_tick)
                if next_init_tick is not None
                else (MIN_SQRT_PRICE_X64 if a_to_b else MAX_SQRT_PRICE_X64)
            )
            sqrt_price_target = (
                max(limit, next_tick_sqrt) if a_to_b else min(limit, next_tick_sqrt)
            )

            step = compute_swap_step(
                amount_remaining,
                pool.fee_rate,
                curr_liquidity,
                curr_sqrt_price,
                sqrt_price_target,
                amount_specified_is_input,
                a_to_b,
            )

            if amount_specified_is_input:
                amount_remaining -= step.amount_in + step.fee_amount
                amount_calculated += step.amount_out
            else:
                amount_remaining -= step.amount_out
                amount_calculated += step.amount_in + step.fee_amount

            fee_sum += step.fee_amount
            curr_protocol_fee, fee_growth_input = self._apply_fees(
                step.fee_amount,
                pool.protocol_fee_rate,
                curr_liquidity,
                curr_protocol_fee,
                fee_growth_input,
            )

            if step.next_sqrt_price == next_tick_sqrt and next_init_tick is not None:
                tick_entry = self._get_tick(next_init_tick)
                if tick_entry is not None and tick_entry.initialized:
                    signed_liq_net = (
                        -tick_entry.liquidity_net if a_to_b else tick_entry.liquidity_net
                    )
                    new_liquidity = curr_liquidity + signed_liq_net
                    if new_liquidity < 0:
                        raise ValueError("liquidity went negative on tick crossing")
                    curr_liquidity = new_liquidity
                    tick_crossings += 1
                    fee_growth_a, fee_growth_b = self._fee_growth_pair(
                        fee_growth_input, a_to_b, pool
                    )
                    tick_entry.fee_growth_outside_a = (
                        fee_growth_a - tick_entry.fee_growth_outside_a
                    ) & ((1 << 128) - 1)
                    tick_entry.fee_growth_outside_b = (
                        fee_growth_b - tick_entry.fee_growth_outside_b
                    ) & ((1 << 128) - 1)
                curr_tick = next_init_tick - 1 if a_to_b else next_init_tick
            elif step.next_sqrt_price != curr_sqrt_price:
                curr_tick = tick_index_from_sqrt_price(step.next_sqrt_price)

            curr_sqrt_price = step.next_sqrt_price

            if amount_remaining == 0 or curr_sqrt_price == sqrt_price_target:
                # Outer loop will re-enter with the new curr_tick if more
                # swap distance is available; if amount_remaining hit zero
                # we'll exit there.
                continue

        if (
            amount_remaining > 0
            and not amount_specified_is_input
            and sqrt_price_limit is None
        ):
            raise ValueError(
                "exact-output swap could not be filled within tick coverage"
            )

        if a_to_b == amount_specified_is_input:
            amount_a = amount - amount_remaining
            amount_b = amount_calculated
        else:
            amount_a = amount_calculated
            amount_b = amount - amount_remaining

        if a_to_b:
            self._pool.fee_growth_global_a = fee_growth_input
            self._pool.protocol_fee_owed_a += curr_protocol_fee
        else:
            self._pool.fee_growth_global_b = fee_growth_input
            self._pool.protocol_fee_owed_b += curr_protocol_fee

        self._pool.liquidity = curr_liquidity
        self._pool.tick_current_index = curr_tick
        self._pool.sqrt_price_x64 = curr_sqrt_price

        if a_to_b:
            self._pool.token_vault_a_amount += amount_a
            self._pool.token_vault_b_amount -= amount_b
        else:
            self._pool.token_vault_a_amount -= amount_a
            self._pool.token_vault_b_amount += amount_b

        if self._pool.token_vault_a_amount < 0 or self._pool.token_vault_b_amount < 0:
            raise ValueError("vault amount went negative — pool out of liquidity")

        lp_fee = fee_sum - curr_protocol_fee
        return {
            "amount_a": amount_a,
            "amount_b": amount_b,
            "lp_fee": lp_fee,
            "fee_total": fee_sum,
            "protocol_fee": curr_protocol_fee,
            "next_liquidity": curr_liquidity,
            "next_tick_index": curr_tick,
            "next_sqrt_price": curr_sqrt_price,
            "fee_growth_global_input": fee_growth_input,
            "fee_growth_global_a": self._pool.fee_growth_global_a,
            "fee_growth_global_b": self._pool.fee_growth_global_b,
            "tick_crossings": tick_crossings,
        }

    @staticmethod
    def _apply_fees(
        fee_amount: int,
        protocol_fee_rate: int,
        curr_liquidity: int,
        curr_protocol_fee: int,
        curr_fee_growth_global_input: int,
    ) -> tuple[int, int]:
        next_protocol_fee = curr_protocol_fee
        global_fee = fee_amount
        if protocol_fee_rate > 0:
            delta = (global_fee * protocol_fee_rate) // PROTOCOL_FEE_DENOMINATOR
            global_fee -= delta
            next_protocol_fee = (next_protocol_fee + delta) & ((1 << 64) - 1)
        next_fee_growth = curr_fee_growth_global_input
        if curr_liquidity > 0:
            next_fee_growth = (
                next_fee_growth + ((global_fee << 64) // curr_liquidity)
            ) & ((1 << 128) - 1)
        return next_protocol_fee, next_fee_growth

    def _fee_growth_pair(
        self, fee_growth_input: int, a_to_b: bool, pool: WhirlpoolPoolState
    ) -> tuple[int, int]:
        return (
            (fee_growth_input, pool.fee_growth_global_b)
            if a_to_b
            else (pool.fee_growth_global_a, fee_growth_input)
        )

    def _next_initialized_tick(self, tick_index: int, a_to_b: bool) -> int | None:
        spacing = self._pool.tick_spacing
        if a_to_b:
            arrays = sorted(self._tick_arrays, key=lambda ta: -ta.start_tick_index)
        else:
            arrays = sorted(self._tick_arrays, key=lambda ta: ta.start_tick_index)
        # Locate the active array (the one containing tick_index).
        for ta in arrays:
            lower = ta.start_tick_index
            upper = ta.start_tick_index + TICK_ARRAY_SIZE * spacing
            if a_to_b:
                # For a-to-b, the search is inclusive of the current tick,
                # so the array hosting tick_index is the natural starting
                # point.
                if lower <= tick_index < upper:
                    found = ta.get_next_init_tick_index(tick_index, spacing, a_to_b=True)
                    if found is not None:
                        return found
                    # No init tick in this array — continue to the next one
                    # to the left (already sorted descending).
                    continue
            else:
                shifted_lower = lower - spacing
                shifted_upper = upper - spacing
                if shifted_lower <= tick_index < shifted_upper:
                    found = ta.get_next_init_tick_index(tick_index, spacing, a_to_b=False)
                    if found is not None:
                        return found
                    continue
        # Walk subsequent arrays in scan direction looking for an init tick.
        # The above already covered the active array — now we need to look in
        # the next-array bucket using its own first/last initialized tick.
        if a_to_b:
            for ta in arrays:
                if ta.start_tick_index >= tick_index:
                    continue
                # Search this array (entirely to the left of tick_index) for
                # any initialized tick, scanning from the right.
                for off in range(TICK_ARRAY_SIZE - 1, -1, -1):
                    if ta.ticks[off].initialized:
                        return ta.start_tick_index + off * spacing
        else:
            for ta in arrays:
                if ta.start_tick_index + TICK_ARRAY_SIZE * spacing <= tick_index:
                    continue
                if ta.start_tick_index <= tick_index:
                    continue
                for off in range(TICK_ARRAY_SIZE):
                    if ta.ticks[off].initialized:
                        return ta.start_tick_index + off * spacing
        return None

    def _get_tick(self, tick_index: int) -> TickEntry | None:
        spacing = self._pool.tick_spacing
        for ta in self._tick_arrays:
            lower = ta.start_tick_index
            upper = ta.start_tick_index + TICK_ARRAY_SIZE * spacing
            if lower <= tick_index < upper:
                try:
                    return ta.get_tick(tick_index, spacing)
                except IndexError:
                    return None
        return None

    # --- Action handlers -----------------------------------------------

    def _execute_swap_action(
        self, action: SwapAction, ctx: ExecutionContext
    ) -> ExecutionResult:
        if action.token_in == action.token_out:
            return ExecutionResult(success=False, error="token_in must differ from token_out")
        if action.token_in not in self._token_ids or action.token_out not in self._token_ids:
            return ExecutionResult(
                success=False,
                error=f"Whirlpool only routes {self._token_a.id}/{self._token_b.id}",
            )
        a_to_b = action.token_in == self._token_a.id
        balance = ctx.agent_state.balance(action.token_in)
        if balance < action.amount_in:
            return ExecutionResult(
                success=False, error=f"insufficient {action.token_in} balance"
            )
        try:
            outcome = self._swap_inplace(
                int(action.amount_in),
                a_to_b=a_to_b,
                amount_specified_is_input=True,
                sqrt_price_limit=None,
            )
        except ValueError as exc:
            return ExecutionResult(success=False, error=str(exc))

        return self._build_result(action.token_in, action.token_out, outcome, a_to_b)

    def _execute_single_asset(
        self, action: SingleAssetAction, ctx: ExecutionContext
    ) -> ExecutionResult:
        # Map BUY (collateral -> asset) / SELL (asset -> collateral) to a
        # SwapAction-shaped call. ``asset`` is what the agent receives on BUY
        # and pays on SELL; ``collateral`` is the other side.
        if action.collateral not in self._token_ids or action.asset not in self._token_ids:
            return ExecutionResult(
                success=False,
                error=f"Whirlpool only routes {self._token_a.id}/{self._token_b.id}",
            )
        if action.asset == action.collateral:
            return ExecutionResult(success=False, error="asset must differ from collateral")

        if action.side == Side.BUY:
            token_in, token_out = action.collateral, action.asset
        else:
            token_in, token_out = action.asset, action.collateral

        balance = ctx.agent_state.balance(token_in)
        if balance < action.amount:
            return ExecutionResult(
                success=False, error=f"insufficient {token_in} balance"
            )
        a_to_b = token_in == self._token_a.id
        try:
            outcome = self._swap_inplace(
                int(action.amount),
                a_to_b=a_to_b,
                amount_specified_is_input=True,
                sqrt_price_limit=None,
            )
        except ValueError as exc:
            return ExecutionResult(success=False, error=str(exc))

        return self._build_result(token_in, token_out, outcome, a_to_b)

    def _build_result(
        self,
        token_in: TokenId,
        token_out: TokenId,
        outcome: dict[str, int],
        a_to_b: bool,
    ) -> ExecutionResult:
        if a_to_b:
            amount_in_raw = outcome["amount_a"]
            amount_out_raw = outcome["amount_b"]
        else:
            amount_in_raw = outcome["amount_b"]
            amount_out_raw = outcome["amount_a"]

        # Pool-side fee accounting: track LP fee in token_in units; expose
        # protocol fee separately on the outcome but credit lp share to
        # ``_accumulated_fees`` to mirror CfammMarket's surface.
        self._accumulated_fees += outcome["lp_fee"]
        # CLMM telemetry: split LP fees by token side and accumulate
        # tick-crossings consumed by the swap. ``a_to_b`` means token_in is
        # token A, so fees were collected in token A.
        if a_to_b:
            self._round_lp_fees_a += outcome["lp_fee"]
        else:
            self._round_lp_fees_b += outcome["lp_fee"]
        self._round_tick_crossings += outcome["tick_crossings"]
        # Track total quote-token volume across the run. ``outcome``
        # always reports both legs; ``amount_b`` is the token-B leg
        # regardless of swap direction.
        self._total_volume_b_raw += int(outcome["amount_b"])

        return ExecutionResult(
            success=True,
            token_deltas={
                token_in: -amount_in_raw,
                token_out: amount_out_raw,
            },
            fees_paid=outcome["fee_total"],
            fee_splits={
                "lp": outcome["lp_fee"],
                "protocol": outcome["protocol_fee"],
            },
            fee_token=token_in,
            volume=amount_in_raw,
        )

    # --- Forkable-market construction ---------------------------------

    @classmethod
    def from_initial_state(
        cls,
        fragments: list[Any],
        *,
        parameters: Any = None,
        numeric_mode: NumericMode | None = None,
    ) -> "WhirlpoolMarket":
        """Build a :class:`WhirlpoolMarket` from parsed fragments.

        Expects exactly one ``kind=="pool"`` fragment for the Whirlpool pool;
        any number of ``kind=="pool_tick_array"`` fragments for tick arrays;
        and per-vault ``kind=="wallet_balance"``-shaped fragments carrying
        the SPL-vault token amounts.
        """
        pool_fragment = None
        tick_array_fragments: list[Any] = []
        vault_fragments: dict[str, dict[str, Any]] = {}
        for f in fragments:
            payload = dict(f.payload)
            payload_kind = payload.get("subkind") or f.kind
            if payload_kind == "pool":
                pool_fragment = f
            elif payload_kind == "pool_tick_array":
                tick_array_fragments.append(f)
            elif payload_kind == "vault_balance":
                vault_pubkey = str(payload.get("pubkey") or f.pubkey)
                vault_fragments[vault_pubkey] = payload
        if pool_fragment is None:
            raise ValueError("WhirlpoolMarket.from_initial_state: missing pool fragment")
        pool_payload = dict(pool_fragment.payload)
        pool_payload.setdefault("pubkey", pool_fragment.pubkey)
        pool = WhirlpoolPoolState(
            pubkey=str(pool_payload["pubkey"]),
            tick_spacing=int(pool_payload["tick_spacing"]),
            fee_rate=int(pool_payload["fee_rate"]),
            protocol_fee_rate=int(pool_payload["protocol_fee_rate"]),
            liquidity=int(pool_payload["liquidity"]),
            sqrt_price_x64=int(pool_payload["sqrt_price_x64"]),
            tick_current_index=int(pool_payload["tick_current_index"]),
            fee_growth_global_a=int(pool_payload.get("fee_growth_global_a", 0)),
            fee_growth_global_b=int(pool_payload.get("fee_growth_global_b", 0)),
            protocol_fee_owed_a=int(pool_payload.get("protocol_fee_owed_a", 0)),
            protocol_fee_owed_b=int(pool_payload.get("protocol_fee_owed_b", 0)),
            token_mint_a=str(pool_payload.get("token_mint_a", "")),
            token_mint_b=str(pool_payload.get("token_mint_b", "")),
            token_vault_a_pubkey=str(pool_payload.get("token_vault_a", "")),
            token_vault_b_pubkey=str(pool_payload.get("token_vault_b", "")),
            token_decimals_a=int(pool_payload.get("token_decimals_a", 0)),
            token_decimals_b=int(pool_payload.get("token_decimals_b", 0)),
        )
        vault_a = vault_fragments.get(pool.token_vault_a_pubkey)
        vault_b = vault_fragments.get(pool.token_vault_b_pubkey)
        if vault_a is not None:
            pool.token_vault_a_amount = int(vault_a.get("amount", 0))
        if vault_b is not None:
            pool.token_vault_b_amount = int(vault_b.get("amount", 0))

        tick_arrays = []
        for f in tick_array_fragments:
            payload = dict(f.payload)
            ticks = [
                TickEntry(
                    initialized=bool(t.get("initialized", False)),
                    liquidity_net=int(t.get("liquidity_net", 0)),
                    liquidity_gross=int(t.get("liquidity_gross", 0)),
                    fee_growth_outside_a=int(t.get("fee_growth_outside_a", 0)),
                    fee_growth_outside_b=int(t.get("fee_growth_outside_b", 0)),
                )
                for t in payload.get("ticks", [])
            ]
            tick_arrays.append(
                TickArrayState(
                    pubkey=str(f.pubkey),
                    start_tick_index=int(payload["start_tick_index"]),
                    ticks=ticks,
                )
            )

        token_a = Token(
            id=str(pool_payload.get("token_a_id", "TOKEN_A")),
            symbol=str(pool_payload.get("token_a_symbol", "TOKEN_A")),
            decimals=int(pool_payload.get("token_decimals_a", 0)),
        )
        token_b = Token(
            id=str(pool_payload.get("token_b_id", "TOKEN_B")),
            symbol=str(pool_payload.get("token_b_symbol", "TOKEN_B")),
            decimals=int(pool_payload.get("token_decimals_b", 0)),
        )
        return cls(
            pool=pool,
            tick_arrays=tick_arrays,
            token_a=token_a,
            token_b=token_b,
            pool_account_id=str(pool_payload.get("pool_account_id") or pool.pubkey),
        )
