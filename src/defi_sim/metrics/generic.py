"""Generic metric implementations.

Ported from quant-simulation, protocol-agnostic.
"""

from __future__ import annotations


import numpy as np

from defi_sim.core.market import Market, PricedMarket
from defi_sim.core.types import (
    AmmSnapshot,
    MarketSnapshot,
    Numeric,
    Side,
    SingleAssetAction,
    ExecutionContext,
    AgentState,
    TokenId,
)


def _iter_snapshots(
    market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
) -> list[MarketSnapshot]:
    if market_state is None:
        return []
    if isinstance(market_state, dict):
        return list(market_state.values())
    return [market_state]


def _extract_token_price(snapshot: MarketSnapshot, token: TokenId) -> float | None:
    prices = getattr(snapshot, "prices", None)
    if isinstance(prices, dict) and token in prices:
        return float(prices[token])

    best_bid = getattr(snapshot, "best_bid", None)
    best_ask = getattr(snapshot, "best_ask", None)
    if isinstance(best_bid, dict) and isinstance(best_ask, dict):
        bid = best_bid.get(token)
        ask = best_ask.get(token)
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
        if bid is not None:
            return float(bid)
        if ask is not None:
            return float(ask)

    return None


def _infer_quote_token(market: Market, token: TokenId) -> TokenId | None:
    books = getattr(market, "_books", None)
    if isinstance(books, dict):
        for base, quote in books.keys():
            if base == token:
                return quote

    collateral_token = getattr(market, "_collateral_token", None)
    token_ids = getattr(market, "_token_ids", None)
    if collateral_token is not None and (token_ids is None or token in token_ids):
        return collateral_token

    # Two-token markets (e.g. WhirlpoolMarket) carry _token_ids but no
    # explicit collateral_token; the counter-side is unambiguous.
    if isinstance(token_ids, (list, tuple)) and len(token_ids) == 2 and token in token_ids:
        return token_ids[0] if token_ids[1] == token else token_ids[1]

    return None


def _trade_budget(trade_amount: Numeric, price_before: Numeric) -> Numeric:
    if isinstance(trade_amount, float) or isinstance(price_before, float):
        notional = float(trade_amount) * float(price_before)
        return max(float(trade_amount) * 10.0, notional * 2.0, 1.0)

    notional = int(trade_amount) * int(price_before)
    return max(int(trade_amount) * 10, notional * 2, 1)


def kl_divergence(p: list[float] | np.ndarray, q: list[float] | np.ndarray) -> float:
    """KL divergence D(p || q). Both distributions must be normalized."""
    p_arr = np.array(p, dtype=float)
    q_arr = np.array(q, dtype=float)

    # Avoid log(0) and 0*log(0)
    mask = (p_arr > 0) & (q_arr > 0)
    if not mask.any():
        return float('inf')

    return float(np.sum(p_arr[mask] * np.log(p_arr[mask] / q_arr[mask])))


def convergence_speed(series: list[float], threshold: float = 0.01) -> int:
    """Return first round where series drops below threshold and stays there."""
    for i, val in enumerate(series):
        if val < threshold:
            # Check it stays below
            if all(v < threshold for v in series[i:]):
                return i
    return len(series)


def convergence_speed_revised(
    series: list[float], threshold: float = 0.01, window: int = 5,
) -> int:
    """Return first round where rolling mean over window drops below threshold."""
    if len(series) < window:
        return len(series)

    for i in range(len(series) - window + 1):
        mean = sum(series[i:i + window]) / window
        if mean < threshold:
            return i
    return len(series)


def compute_slippage(
    market: Market,
    token: TokenId,
    trade_fraction: float = 0.01,
) -> float:
    """Compute slippage by executing a test trade on a copy.
    Returns slippage as a fraction (0 = no slippage)."""
    if not isinstance(market, PricedMarket):
        return 0.0

    clone = market.copy()
    prices = clone.get_prices()
    price_before = prices.get(token, 0)
    if price_before <= 0:
        return 0.0

    depth = clone.get_depth(token)
    if isinstance(depth, float):
        trade_amount = depth * trade_fraction
    else:
        trade_amount = int(depth * trade_fraction)

    if trade_amount <= 0:
        return 0.0

    quote_token = _infer_quote_token(clone, token)
    if quote_token is None:
        return 0.0

    # Execute test trade
    budget = _trade_budget(trade_amount, price_before)
    dummy_agent = AgentState(agent_id="__test__", balances={quote_token: budget})
    ctx = ExecutionContext(agent_state=dummy_agent)
    action = SingleAssetAction(
        agent_id="__test__", asset=token, collateral=quote_token,
        amount=trade_amount, side=Side.BUY,
    )
    result = clone.execute(action, ctx)

    if not result.success:
        return 1.0  # Total slippage (couldn't execute)

    new_prices = clone.get_prices()
    price_after = new_prices.get(token, 0)

    if isinstance(price_before, float):
        return abs(price_after - price_before) / price_before if price_before > 0 else 0.0
    else:
        return abs(price_after - price_before) / price_before if price_before > 0 else 0.0


def lp_profitability(
    fees_earned: Numeric,
    capital_deposited: Numeric,
    impermanent_loss: Numeric = 0,
) -> float:
    """LP profitability ratio: (fees - IL) / capital."""
    if capital_deposited <= 0:
        return 0.0
    if isinstance(fees_earned, float):
        return (fees_earned - impermanent_loss) / capital_deposited
    return (fees_earned - impermanent_loss) / capital_deposited


def manipulation_cost(budget: Numeric, effect: Numeric) -> float:
    """Cost-effectiveness of manipulation: budget / effect."""
    if effect <= 0:
        return float('inf')
    return float(budget) / float(effect)


def manipulation_resistance_revised(
    budget: Numeric,
    price_change: Numeric,
    payout_improvement: Numeric = 0,
) -> float:
    """Revised manipulation resistance: budget / (price_change + payout_improvement)."""
    total_effect = float(price_change) + float(payout_improvement)
    if total_effect <= 0:
        return float('inf')
    return float(budget) / total_effect


def exitability(market: Market, holdings: dict[TokenId, Numeric]) -> float:
    """Measure how well an agent can exit positions.
    Returns fraction of holdings that can be liquidated."""
    if not isinstance(market, PricedMarket):
        return 0.0

    clone = market.copy()
    total_value = 0.0
    liquidated_value = 0.0

    for token, amount in holdings.items():
        if amount <= 0:
            continue

        total_value += float(amount)
        quote_token = _infer_quote_token(clone, token)
        if quote_token is None:
            continue

        dummy_agent = AgentState(agent_id="__test__", balances={token: amount})
        ctx = ExecutionContext(agent_state=dummy_agent)
        action = SingleAssetAction(
            agent_id="__test__", asset=token, collateral=quote_token,
            amount=amount, side=Side.SELL,
        )
        result = clone.execute(action, ctx)
        if result.success:
            liquidated_value += float(amount)

    return liquidated_value / total_value if total_value > 0 else 1.0


# --- Streaming metrics ---


class MaxDrawdown:
    """Track maximum peak-to-trough decline in total market value."""

    def __init__(self, token: TokenId | None = None):
        self._token = token
        self._peak: float = 0.0
        self._max_dd: float = 0.0

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        snapshots = _iter_snapshots(market_state)
        if not snapshots:
            return
        value = sum(
            float(snapshot.total_liquidity)
            for snapshot in snapshots
            if isinstance(snapshot, AmmSnapshot)
        )
        if value <= 0:
            return

        if value > self._peak:
            self._peak = value
        if self._peak > 0:
            dd = (self._peak - value) / self._peak
            if dd > self._max_dd:
                self._max_dd = dd

    def finalize(self) -> float:
        return self._max_dd


class RollingVolatility:
    """Track rolling price volatility over a configurable window."""

    def __init__(self, token: TokenId, window: int = 20):
        self._token = token
        self._window = window
        self._prices: list[float] = []

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        prices = [
            price
            for snapshot in _iter_snapshots(market_state)
            if (price := _extract_token_price(snapshot, self._token)) is not None
        ]
        if not prices:
            return
        self._prices.append(sum(prices) / len(prices))

    def finalize(self) -> float:
        if len(self._prices) < 2:
            return 0.0
        returns = np.diff(np.log(np.maximum(self._prices, 1e-10)))
        if len(returns) < self._window:
            return float(np.std(returns))
        # Rolling std of last window
        return float(np.std(returns[-self._window:]))


class LPInRangeFraction:
    """Fraction of rounds the LP's range covered the spot tick.

    Walks all concentrated-liquidity positions on the bound market each
    round and counts the in-range share. Output is a single
    pool-averaged fraction in [0, 1]; per-LP results live on the
    engine's ``derived_metrics`` keyed by agent_id.
    """

    def __init__(self, market: Any | None = None):
        self._market = market
        self._in_range_count: int = 0
        self._total_count: int = 0

    def bind_market(self, market: Any) -> None:
        self._market = market

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        if self._market is None:
            return
        positions = []
        get_all = getattr(self._market, "get_all_lp_positions", None)
        if callable(get_all):
            positions = list(get_all())
        if not positions:
            return
        for pos in positions:
            in_range = getattr(pos, "in_range", None)
            if in_range is None:
                continue
            self._total_count += 1
            if in_range:
                self._in_range_count += 1

    def finalize(self) -> float:
        if self._total_count <= 0:
            return 0.0
        return self._in_range_count / self._total_count


class RangeIL:
    """Average range-bounded impermanent loss across all CLMM positions.

    For each round, computes ``1 - position_value_now / hodl_value_now``
    using the bound market's ``position_value_in_b`` /
    ``hodl_value_in_b`` helpers (Whirlpool exposes these). Output is the
    mean IL across all sampled (round, position) pairs, in [0, 1].
    """

    def __init__(self, market: Any | None = None):
        self._market = market
        self._sum: float = 0.0
        self._count: int = 0

    def bind_market(self, market: Any) -> None:
        self._market = market

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        if self._market is None:
            return
        records_fn = getattr(self._market, "all_position_records", None)
        value_fn = getattr(self._market, "position_value_in_b", None)
        hodl_fn = getattr(self._market, "hodl_value_in_b", None)
        if not callable(records_fn) or not callable(value_fn) or not callable(hodl_fn):
            return
        for pos in records_fn():
            try:
                value_now = float(value_fn(pos))
                value_hodl = float(hodl_fn(pos))
            except Exception:
                continue
            if value_hodl <= 0:
                continue
            il = max(0.0, 1.0 - value_now / value_hodl)
            self._sum += il
            self._count += 1

    def finalize(self) -> float:
        if self._count <= 0:
            return 0.0
        return self._sum / self._count


class FeesVsILBreakeven:
    """Average ``fees_in_quote / |range_il_in_quote|`` across CLMM positions.

    Values > 1 mean the LP's collected fees more than offset their
    IL. Returns ``+inf`` when all sampled positions have zero IL but
    non-zero fees, and ``0`` when no fees were collected.
    """

    def __init__(self, market: Any | None = None):
        self._market = market
        self._ratios: list[float] = []
        self._all_zero_il_with_fees: bool = False

    def bind_market(self, market: Any) -> None:
        self._market = market

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        if self._market is None:
            return
        records_fn = getattr(self._market, "all_position_records", None)
        value_fn = getattr(self._market, "position_value_in_b", None)
        hodl_fn = getattr(self._market, "hodl_value_in_b", None)
        if not callable(records_fn) or not callable(value_fn) or not callable(hodl_fn):
            return
        pool = getattr(self._market, "pool", None)
        sqrt_p = int(getattr(pool, "sqrt_price_x64", 0))
        for pos in records_fn():
            try:
                value_now = float(value_fn(pos))
                value_hodl = float(hodl_fn(pos))
            except Exception:
                continue
            if value_hodl <= 0:
                continue
            il_in_b = max(0.0, value_hodl - value_now)
            fees_a_in_b = (int(pos.accumulated_fees_a) * sqrt_p * sqrt_p) // (1 << 128)
            fees_in_b = float(int(pos.accumulated_fees_b) + fees_a_in_b)
            if il_in_b > 0:
                self._ratios.append(fees_in_b / il_in_b)
            elif fees_in_b > 0:
                self._all_zero_il_with_fees = True

    def finalize(self) -> float:
        if not self._ratios:
            return float("inf") if self._all_zero_il_with_fees else 0.0
        return sum(self._ratios) / len(self._ratios)


class TWAP:
    """Time-weighted average price."""

    def __init__(self, token: TokenId):
        self._token = token
        self._weighted_sum: float = 0.0
        self._total_time: float = 0.0
        self._last_ts: int | None = None

    def on_round(
        self,
        round: int,
        timestamp: int,
        market_state: MarketSnapshot | dict[str, MarketSnapshot] | None,
    ) -> None:
        prices = [
            price
            for snapshot in _iter_snapshots(market_state)
            if (price := _extract_token_price(snapshot, self._token)) is not None
        ]
        if not prices:
            return
        price = sum(prices) / len(prices)
        if self._last_ts is not None:
            dt = timestamp - self._last_ts
            self._weighted_sum += price * dt
            self._total_time += dt
        self._last_ts = timestamp

    def finalize(self) -> float:
        if self._total_time <= 0:
            return 0.0
        return self._weighted_sum / self._total_time
