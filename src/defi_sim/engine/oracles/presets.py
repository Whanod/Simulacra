"""Calibrated ``PullOracle`` presets for 2026 Solana mainnet feeds (PRD US-006 line 484).

All numeric parameters are placeholders pending the Phase 2.4 calibration
pass — every value below is annotated ``# CALIBRATE-2.4`` so the calibration
sweep can locate them. ``price_source`` is callable injection: tests pass a
lambda over a synthetic price path; production code passes the canonical
"truth" feed for the symbol.
"""

from __future__ import annotations

from collections.abc import Callable

from defi_sim.core.types import Numeric
from defi_sim.engine.oracles.source import PullOracle


def pyth_pull_solusdc(
    price_source: Callable[[int], Numeric],
    *,
    confidence_interval: float = 0.10,  # CALIBRATE-2.4 — typical SOL/USD band ($)
    initial_pull_slot: int | None = None,
) -> PullOracle:
    """Pyth Pull SOL/USDC preset.

    Pyth Pull is the dominant 2026 Solana oracle: consumers include a
    Wormhole-attested update instruction in their tx. Cadence is bounded
    by Pythnet aggregation (~400ms ≈ 1 Solana slot of freshness on the
    source side) but staleness on-chain depends on the consumer's
    re-pull rhythm.
    """
    return PullOracle(
        oracle_id="pyth_pull_sol_usdc",
        update_cu_cost=40_000,  # CALIBRATE-2.4 — VAA verify + price update CU
        update_lamport_cost=5_000,  # CALIBRATE-2.4 — single-sig tx fee floor
        staleness_tolerance_slots=25,  # CALIBRATE-2.4 — ~10s typical consumer tolerance
        price_source=price_source,
        confidence_interval=confidence_interval,
        initial_pull_slot=initial_pull_slot,
    )


def pyth_lazer_solusdc(
    price_source: Callable[[int], Numeric],
    *,
    confidence_interval: float = 0.05,  # CALIBRATE-2.4 — tighter band on faster feed
    initial_pull_slot: int | None = None,
) -> PullOracle:
    """Pyth Lazer SOL/USDC preset.

    Pyth Lazer offers sub-slot freshness via a separate low-latency
    relay. Consumers pay slightly more CU for the verification but can
    re-pull every slot if the use case demands it (perp funding,
    high-frequency liquidations).
    """
    return PullOracle(
        oracle_id="pyth_lazer_sol_usdc",
        update_cu_cost=55_000,  # CALIBRATE-2.4 — Lazer verify CU > Pull verify CU
        update_lamport_cost=5_000,  # CALIBRATE-2.4
        staleness_tolerance_slots=2,  # CALIBRATE-2.4 — sub-second design target
        price_source=price_source,
        confidence_interval=confidence_interval,
        initial_pull_slot=initial_pull_slot,
    )


def switchboard_on_demand_solusdc(
    price_source: Callable[[int], Numeric],
    *,
    confidence_interval: float = 0.15,  # CALIBRATE-2.4 — typical SB OD band
    initial_pull_slot: int | None = None,
) -> PullOracle:
    """Switchboard On-Demand SOL/USDC preset.

    Switchboard On-Demand replaces the legacy V2 crank with a TEE-backed
    pull model. Consumers gossip-fetch a signed quote and submit it as
    part of their tx. Custom feeds (LST exchange rates, perp marks)
    overwhelmingly use this path in 2026.
    """
    return PullOracle(
        oracle_id="switchboard_on_demand_sol_usdc",
        update_cu_cost=35_000,  # CALIBRATE-2.4 — TEE quote verify CU
        update_lamport_cost=5_000,  # CALIBRATE-2.4
        staleness_tolerance_slots=20,  # CALIBRATE-2.4 — comparable to Pyth Pull
        price_source=price_source,
        confidence_interval=confidence_interval,
        initial_pull_slot=initial_pull_slot,
    )


__all__ = [
    "pyth_pull_solusdc",
    "pyth_lazer_solusdc",
    "switchboard_on_demand_solusdc",
]
