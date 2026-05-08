"""Built-in fee model implementations.

Five fee models ported from quant-simulation, updated to return FeeResult
with configurable splits.
"""

from __future__ import annotations

from defi_sim.core.types import AmmSnapshot, ExecutionContext, Numeric
from defi_sim.fees.types import FeeResult


def _apply_splits(total_fee: Numeric, split_config: dict[str, int]) -> dict[str, Numeric]:
    """Distribute total_fee according to split_config (bps summing to 10000)."""
    result: dict[str, Numeric] = {}
    for dest, bps in split_config.items():
        if isinstance(total_fee, float):
            result[dest] = total_fee * bps / 10000
        else:
            result[dest] = (total_fee * bps) // 10000
    return result


def _reserve_imbalance(ctx: ExecutionContext) -> float:
    market_state = ctx.market_state
    if not isinstance(market_state, AmmSnapshot) or not market_state.reserves:
        return 0.0
    reserves = [float(value) for value in market_state.reserves.values()]
    mean_reserve = sum(reserves) / len(reserves)
    if mean_reserve <= 0:
        return 0.0
    return max(abs(reserve - mean_reserve) / mean_reserve for reserve in reserves)


def _relative_spread(ctx: ExecutionContext) -> float:
    market_state = ctx.market_state
    if market_state is None:
        return 0.0

    spread_map = getattr(market_state, "spread", None)
    best_ask_map = getattr(market_state, "best_ask", None)
    if not isinstance(spread_map, dict) or not isinstance(best_ask_map, dict):
        return 0.0

    ratios: list[float] = []
    for token, spread in spread_map.items():
        best_ask = best_ask_map.get(token)
        if best_ask is None or best_ask <= 0:
            continue
        ratios.append(float(spread) / float(best_ask))
    return max(ratios, default=0.0)


def flat_fee(
    gross: Numeric,
    ctx: ExecutionContext,
    trade_fee_bps: int = 30,
    split_config: dict[str, int] | None = None,
) -> FeeResult:
    """Constant basis-point fee on every trade."""
    if split_config is None:
        split_config = {"lp": 5000, "protocol": 5000}

    if isinstance(gross, float):
        total_fee = gross * trade_fee_bps / 10000
    else:
        total_fee = (gross * trade_fee_bps) // 10000

    return FeeResult(
        total_fee=total_fee,
        splits=_apply_splits(total_fee, split_config),
        net_amount=gross - total_fee,
    )


def dynamic_fee(
    gross: Numeric,
    ctx: ExecutionContext,
    base_bps: int = 30,
    max_bps: int = 100,
    volatility_multiplier: float = 2.0,
    split_config: dict[str, int] | None = None,
) -> FeeResult:
    """State-dependent fee that increases with market imbalance.
    Reads market snapshot for reserve variance as a proxy for volatility."""
    if split_config is None:
        split_config = {"lp": 5000, "protocol": 5000}

    imbalance = _reserve_imbalance(ctx)
    spread = _relative_spread(ctx)
    market_stress = max(imbalance, spread)
    effective_bps = int(
        base_bps + (max_bps - base_bps) * min(1.0, market_stress * volatility_multiplier)
    )
    effective_bps = min(effective_bps, max_bps)

    if isinstance(gross, float):
        total_fee = gross * effective_bps / 10000
    else:
        total_fee = (gross * effective_bps) // 10000

    return FeeResult(
        total_fee=total_fee,
        splits=_apply_splits(total_fee, split_config),
        net_amount=gross - total_fee,
    )


def tiered_fee(
    gross: Numeric,
    ctx: ExecutionContext,
    base_bps: int = 30,
    tiers: list[tuple[Numeric, int]] | None = None,
    split_config: dict[str, int] | None = None,
) -> FeeResult:
    """Volume-tiered fee. Lower fees for higher cumulative volume.
    Reads ctx.agent_state.cumulative_volume for tier selection."""
    if split_config is None:
        split_config = {"lp": 5000, "protocol": 5000}
    if tiers is None:
        # (volume_threshold, fee_bps) — sorted ascending by threshold
        tiers = [
            (0, base_bps),
            (1_000_000_000_000, 20),  # >1000 tokens: 20 bps
            (10_000_000_000_000, 10),  # >10000 tokens: 10 bps
        ]

    volume = ctx.agent_state.cumulative_volume if ctx.agent_state else 0
    effective_bps = base_bps
    for threshold, bps in tiers:
        if volume >= threshold:
            effective_bps = bps

    if isinstance(gross, float):
        total_fee = gross * effective_bps / 10000
    else:
        total_fee = (gross * effective_bps) // 10000

    return FeeResult(
        total_fee=total_fee,
        splits=_apply_splits(total_fee, split_config),
        net_amount=gross - total_fee,
    )


def spread_fee(
    gross: Numeric,
    ctx: ExecutionContext,
    base_bps: int = 30,
    spread_multiplier: float = 1.5,
    split_config: dict[str, int] | None = None,
) -> FeeResult:
    """Fee that scales with market spread / imbalance."""
    if split_config is None:
        split_config = {"lp": 5000, "protocol": 5000}

    spread = _relative_spread(ctx)
    spread_factor = 1.0 + min(spread * spread_multiplier, spread_multiplier)
    effective_bps = max(base_bps, int(base_bps * spread_factor))

    if isinstance(gross, float):
        total_fee = gross * effective_bps / 10000
    else:
        total_fee = (gross * effective_bps) // 10000

    return FeeResult(
        total_fee=total_fee,
        splits=_apply_splits(total_fee, split_config),
        net_amount=gross - total_fee,
    )


def time_weighted_fee(
    gross: Numeric,
    ctx: ExecutionContext,
    base_bps: int = 10,
    max_bps: int = 50,
    split_config: dict[str, int] | None = None,
) -> FeeResult:
    """Fee that increases as the simulation progresses (approaching resolution).
    Reads ctx.current_round and ctx.total_rounds."""
    if split_config is None:
        split_config = {"lp": 5000, "protocol": 5000}

    progress = ctx.current_round / max(ctx.total_rounds, 1)
    effective_bps = int(base_bps + (max_bps - base_bps) * progress)
    effective_bps = min(effective_bps, max_bps)

    if isinstance(gross, float):
        total_fee = gross * effective_bps / 10000
    else:
        total_fee = (gross * effective_bps) // 10000

    return FeeResult(
        total_fee=total_fee,
        splits=_apply_splits(total_fee, split_config),
        net_amount=gross - total_fee,
    )
