"""Static experiment template catalog for guided flows."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

LEGACY_TEMPLATE_ALIASES: dict[str, str] = {
    "amm-fee-tuning": "whirlpool-fee-tuning",
    "mev-stress-test": "solana-sandwich-stress",
    "lp-sustainability": "dlmm-bin-sustainability",
    "cross-market-arbitrage": "raydium-vs-whirlpool-arb",
}


def resolve_template_id(template_id: str) -> str:
    """Map a (possibly legacy) template ID to its canonical form.

    Legacy IDs from the chain-neutral era resolve to their Solana-named
    counterparts and emit a deprecation log line. Unknown IDs pass through
    untouched so the caller can decide whether to 404.
    """
    canonical = LEGACY_TEMPLATE_ALIASES.get(template_id)
    if canonical is None:
        return template_id
    logger.warning(
        "template_id %r is a deprecated alias; use %r instead",
        template_id,
        canonical,
    )
    return canonical


def find_template(template_id: str) -> dict[str, Any] | None:
    """Look up a template by ID, transparently resolving legacy aliases."""
    canonical = resolve_template_id(template_id)
    for template in experiment_templates():
        if template["template_id"] == canonical:
            return template
    return None


def experiment_templates() -> list[dict[str, Any]]:
    sol_usdc_tokens = [
        {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
        {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
    ]
    return [
        {
            "template_id": "whirlpool-fee-tuning",
            "name": "Whirlpool fee tuning",
            "description": (
                "Sweep across Whirlpool-style fee tiers (0.01%, 0.05%, 0.30%, 1%) on a SOL/USDC pool "
                "to inspect LP revenue versus trader cost. Synthetic math: real Whirlpool is CLMM; "
                "this scaffold runs L2-norm CFAMM until Phase 3.1."
            ),
            "base_spec": {
                "market": {
                    "type": "cfamm",
                    "tokens": sol_usdc_tokens,
                    "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
                    "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
                },
                "agents": [
                    {
                        "type": "noise",
                        "agent_id": "noise-1",
                        "params": {"collateral": "USDC", "frequency": 0.2},
                        "initial_balances": {"USDC": 1_000_000_000},
                    }
                ],
                "num_rounds": 20,
                "snapshot_interval": 1,
                "seed": 42,
            },
            "fee_tier_presets_bps": [1, 5, 30, 100],
            "editable_fields": [
                "market.fee_model.params.trade_fee_bps",
                "market.params.initial_liquidity",
                "agents[0].params.frequency",
                "num_rounds",
            ],
            "recommended_metrics": ["final_yes_price", "num_rounds_executed", "stopped_early"],
            "synthetic_mode": True,
            "synthetic_math_model": "l2_norm_cfamm",
            "non_transferable_conclusions": [
                "Fee-tier rankings (which tier earns the most LP revenue) may flip on real Whirlpool CLMM. Slippage at depth differs qualitatively. Do not use these results to pick a mainnet fee tier.",
            ],
        },
        {
            "template_id": "solana-sandwich-stress",
            "name": "Solana sandwich stress",
            "description": (
                "SOL/USDC pool under priority-ordered execution with a sandwich-style manipulator, "
                "passive LP, and noise flow. Synthetic math: real Solana sandwich economics depend on "
                "Raydium/Whirlpool slippage curves and Jito bundle semantics — both land in Phase 1.7 / 3.1."
            ),
            "base_spec": {
                "market": {
                    "type": "cfamm",
                    "tokens": sol_usdc_tokens,
                    "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
                    "params": {"initial_liquidity": 2_000_000, "collateral_token": "USDC"},
                },
                "agents": [
                    {
                        "type": "noise",
                        "agent_id": "noise-1",
                        "params": {"collateral": "USDC", "frequency": 0.25, "bundle_probability": 0.0, "trade_min": 100_000, "trade_max": 5_000_000, "bidirectional": True, "fee_elasticity": 1.0, "reference_fee_bps": 30.0},
                        "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-2",
                        "params": {"collateral": "USDC", "frequency": 0.25},
                        "initial_balances": {"USDC": 500_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-3",
                        "params": {"collateral": "USDC", "frequency": 0.25},
                        "initial_balances": {"USDC": 500_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-4",
                        "params": {"collateral": "USDC", "frequency": 0.25},
                        "initial_balances": {"USDC": 500_000_000},
                    },
                    {
                        "type": "manipulator",
                        "agent_id": "sandwich-1",
                        "params": {"collateral": "USDC"},
                        "initial_balances": {"USDC": 1_000_000_000},
                    },
                    {
                        "type": "passive_lp",
                        "agent_id": "lp-1",
                        "params": {"collateral": "USDC"},
                        "initial_balances": {"USDC": 2_000_000_000},
                    },
                ],
                "execution": {
                    "type": "solana_like",
                    "ordering": {"type": "priority"},
                    "gas_model": {"type": "compute_unit", "params": {}},
                },
                "num_rounds": 20,
                "snapshot_interval": 1,
                "seed": 99,
            },
            "editable_fields": [
                "execution.ordering.type",
                "market.params.initial_liquidity",
                "agents[4].initial_balances.USDC",
                "num_rounds",
            ],
            "recommended_metrics": ["final_yes_price", "price_dislocation", "stopped_early"],
            "synthetic_mode": True,
            "synthetic_math_model": "l2_norm_cfamm",
            "non_transferable_conclusions": [
                "Sandwich profitability is derived from L2-norm slippage curves; real Raydium/Whirlpool sandwich economics differ in both directions and magnitude. Wait for 3.1 before extrapolating.",
            ],
        },
        {
            "template_id": "dlmm-bin-sustainability",
            "name": "DLMM bin sustainability",
            "description": (
                "Single-sided LP on a SOL/USDC pool to inspect inventory drift and IL exposure under noise "
                "flow. Synthetic math: real Meteora DLMM is bin-based with discrete liquidity migration — "
                "this scaffold runs L2-norm CFAMM until Phase 3.1."
            ),
            "base_spec": {
                "market": {
                    "type": "cfamm",
                    "tokens": sol_usdc_tokens,
                    "fee_model": {"type": "flat", "params": {"trade_fee_bps": 25}},
                    "params": {"initial_liquidity": 5_000_000, "collateral_token": "USDC"},
                },
                "agents": [
                    {
                        "type": "passive_lp",
                        "agent_id": "lp-1",
                        "params": {"collateral": "USDC", "deposit_fraction": 0.95},
                        "initial_balances": {"USDC": 2_000_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-1",
                        "params": {"collateral": "USDC", "frequency": 0.2},
                        "initial_balances": {"USDC": 1_000_000_000},
                    },
                ],
                "num_rounds": 30,
                "snapshot_interval": 1,
                "seed": 21,
            },
            "editable_fields": [
                "market.params.initial_liquidity",
                "market.fee_model.params.trade_fee_bps",
                "agents[0].params.deposit_fraction",
                "agents[0].initial_balances.USDC",
                "num_rounds",
            ],
            "recommended_metrics": ["lp_final_balance", "final_yes_price", "num_rounds_executed"],
            "synthetic_mode": True,
            "synthetic_math_model": "l2_norm_cfamm",
            "non_transferable_conclusions": [
                "Single-sided IL on L2-norm is not single-sided IL on DLMM bins. Real DLMM bin migration costs and out-of-range behaviour are absent here.",
            ],
        },
        {
            "template_id": "raydium-vs-whirlpool-arb",
            "name": "Raydium vs Whirlpool arbitrage",
            "description": (
                "Two SOL/USDC pools (Raydium-shaped and Whirlpool-shaped) at slightly different depths, "
                "with a Jupiter-style arbitrageur closing the gap. Synthetic math: real Raydium AMM v4 is "
                "xy=k and Whirlpool is CLMM — both legs run L2-norm CFAMM here until Phase 3.1."
            ),
            "base_spec": {
                "market": {
                    "type": "world",
                    "markets": {
                        "raydium": {
                            "type": "cfamm",
                            "tokens": sol_usdc_tokens,
                            "fee_model": {"type": "flat", "params": {"trade_fee_bps": 25}},
                            "params": {"initial_liquidity": 1_000_000, "collateral_token": "USDC"},
                        },
                        "whirlpool": {
                            "type": "cfamm",
                            "tokens": sol_usdc_tokens,
                            "fee_model": {"type": "flat", "params": {"trade_fee_bps": 5}},
                            "params": {"initial_liquidity": 1_200_000, "collateral_token": "USDC"},
                        },
                    },
                },
                "agents": [
                    {
                        "type": "arbitrageur",
                        "agent_id": "arb-1",
                        "params": {"collateral": "USDC"},
                        "initial_balances": {"USDC": 1_000_000_000},
                    }
                ],
                "num_rounds": 25,
                "snapshot_interval": 1,
                "seed": 17,
            },
            "editable_fields": [
                "market.markets.raydium.params.initial_liquidity",
                "market.markets.whirlpool.params.initial_liquidity",
                "market.markets.raydium.fee_model.params.trade_fee_bps",
                "market.markets.whirlpool.fee_model.params.trade_fee_bps",
                "agents[0].initial_balances.USDC",
                "num_rounds",
            ],
            "recommended_metrics": ["amm_yes_price", "book_yes_price", "num_rounds_executed"],
            "synthetic_mode": True,
            "synthetic_math_model": "l2_norm_cfamm",
            "non_transferable_conclusions": [
                "Arb opportunity sizing, optimal trade chunking, and price-impact convergence are derived from L2-norm geometry. Real xy=k vs. CLMM arb edges differ in shape, not just magnitude. Do not use these results to size mainnet arb capital.",
            ],
        },
        {
            "template_id": "solana-sandwich-lighthouse",
            "name": "Orca SOL/USDC Whirlpool simulation",
            "description": (
                "Protocol: Orca Whirlpool CLMM on SOL/USDC — concentrated liquidity "
                "with sqrt-price/tick math and a 0.30% swap fee. "
                "Real pool, tick-array, and vault state are hydrated from a captured "
                "mainnet slot, so depth and price come from the live book rather than "
                "a curve approximation. "
                "Simulation: Solana slot clock with leader rotation, priority-fee "
                "market, Jito bundle auction, compute-unit accounting, and ALT "
                "compression. Noise traders and swap-flow agents drive order flow on "
                "top of the real pool, with a Jito searcher participating in the "
                "bundle auction."
            ),
            "base_spec": {
                # Opt into role-based agent rescaling. With this flag set,
                # ``apply_lighthouse_sizing`` (in defi_sim_api.backend.
                # lighthouse_sizing) rewrites trade sizes and balances at
                # template-fetch and run time as fractions of the captured
                # token-B vault depth. This is what makes the
                # ``initial_liquidity`` slider actually drive flow — without
                # it, raw integer trade caps stay pinned to whatever the
                # template author baked in even as pool depth scales 10×.
                "lighthouse_sizing": True,
                "market": {
                    "type": "whirlpool",
                    "tokens": sol_usdc_tokens,
                    # Default to 30 bps (matches the captured pool's on-chain
                    # fee_rate). The Builder's Fee model panel writes here
                    # too; ``_build_whirlpool_market`` honors flat overrides
                    # by overwriting ``pool.fee_rate`` post-hydration.
                    "fee_model": {"type": "flat", "params": {"trade_fee_bps": 30}},
                    "params": {
                        # Captured at slot 417595698 from the canonical
                        # SOL/USDC Whirlpool. Re-snapshot via
                        # tools/cache_corpus_slot.py + the lighthouse
                        # capture script in tools/snapshotter/ when the
                        # corpus is refreshed.
                        "corpus_slot": 417595698,
                        "pool_pubkey": "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
                        "pool_account_id": "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
                        "token_a_id": "SOL",
                        "token_b_id": "USDC",
                        "token_a_symbol": "SOL",
                        "token_b_symbol": "USDC",
                        # Captured token-B (USDC) vault depth at slot
                        # 417595698 in human units. Pinned so a default
                        # load is a no-op; raising the slider scales pool
                        # L, both vaults, and per-tick liquidities by
                        # ``target / 127_514``.
                        "initial_liquidity": 127_514,
                    },
                },
                # PRD US-001 selection criterion #1 ("Slot clock + leader
                # schedule"): without this the engine falls back to BlockClock
                # and snapshots report current_slot=None / current_leader=None,
                # so the lighthouse silently fails to exercise the slot clock.
                "clock": {
                    "type": "solana_slot",
                    "params": {
                        "slot_duration_seconds": 0.4,
                        "epoch_length_slots": 432_000,
                        "skip_rate": 0.0,
                        "genesis": 0,
                        "seed": 1337,
                    },
                },
                "agents": [
                    {
                        "type": "noise",
                        "agent_id": "noise-1",
                        "params": {"collateral": "USDC", "frequency": 0.25, "bundle_probability": 0.0, "trade_min": 100_000, "trade_max": 5_000_000, "bidirectional": True, "fee_elasticity": 1.0, "reference_fee_bps": 30.0},
                        "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-2",
                        "params": {"collateral": "USDC", "frequency": 0.25, "bundle_probability": 0.0, "trade_min": 100_000, "trade_max": 5_000_000, "bidirectional": True, "fee_elasticity": 1.0, "reference_fee_bps": 30.0},
                        "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-3",
                        "params": {"collateral": "USDC", "frequency": 0.25, "bundle_probability": 0.0, "trade_min": 100_000, "trade_max": 5_000_000, "bidirectional": True, "fee_elasticity": 1.0, "reference_fee_bps": 30.0},
                        "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
                    },
                    {
                        "type": "noise",
                        "agent_id": "noise-4",
                        "params": {"collateral": "USDC", "frequency": 0.25, "bundle_probability": 0.0, "trade_min": 100_000, "trade_max": 5_000_000, "bidirectional": True, "fee_elasticity": 1.0, "reference_fee_bps": 30.0},
                        "initial_balances": {"USDC": 500_000_000, "SOL": 5_000_000_000},
                    },
                    {
                        "type": "swap_noise",
                        "agent_id": "victim-1",
                        # ``sizing_role`` selects the "swap_noise:large" row
                        # in ROLE_FRACTIONS. amount_min/max + balances are
                        # overwritten at run time as fractions of pool depth.
                        "sizing_role": "swap_noise:large",
                        "params": {
                            "token_in": "USDC",
                            "token_out": "SOL",
                            "amount_min": 500_000,
                            "amount_max": 25_000_000,
                            "frequency": 0.5,
                            "cu_price_min": 1_000,
                            "cu_price_max": 80_000,
                            # Lower fees → larger victim swaps. Hooks the
                            # 30→15 bps protocol-design counterfactual into
                            # the volume metric — without this, victim flow
                            # is fee-blind and ``total_volume_quote`` stays
                            # flat across fee tiers.
                            "fee_elasticity": 1.0,
                            "reference_fee_bps": 30.0,
                        },
                        "initial_balances": {
                            "USDC": 1_000_000_000,
                            "SOL": 10_000_000_000,
                        },
                    },
                    {
                        "type": "swap_noise",
                        "agent_id": "victim-small",
                        "sizing_role": "swap_noise:small",
                        "params": {
                            "token_in": "USDC",
                            "token_out": "SOL",
                            "amount_min": 50_000,
                            "amount_max": 1_500_000,
                            "frequency": 0.7,
                            "cu_price_min": 100,
                            "cu_price_max": 30_000,
                            "fee_elasticity": 1.0,
                            "reference_fee_bps": 30.0,
                        },
                        "initial_balances": {
                            "USDC": 500_000_000,
                            "SOL": 5_000_000_000,
                        },
                    },
                    {
                        "type": "manipulator",
                        "agent_id": "sandwich-1",
                        # Tranches sized to the real pool depth — 0.4 % of
                        # vault per tranche keeps slippage in the 1–2 % band.
                        "params": {
                            "collateral": "USDC",
                            "budget": 50_000_000,
                            "num_tranches": 50,
                            "spend_fraction": 0.01,
                        },
                        "initial_balances": {"USDC": 500_000_000},
                    },
                    {
                        "type": "passive_lp",
                        "agent_id": "lp-passive",
                        # Passive LP: deposit once, ride out drift, only
                        # exit if IL exceeds ``max_loss_threshold`` (5%).
                        # Range chosen wide enough (±10%) that the
                        # template's per-round flow rarely trips the
                        # threshold — produces a flat agent-L line in
                        # the Total LP Deposits chart, the contrast
                        # against the rebalancing variant.
                        "params": {
                            "collateral": "USDC",
                            "range_mode": "symmetric_pct",
                            "range_width_pct": 0.10,
                            "rebalance_on_exit": False,
                        },
                        "initial_balances": {"USDC": 2_000_000_000, "SOL": 10_000_000_000},
                    },
                    {
                        "type": "rebalancing_lp",
                        "agent_id": "lp-rebalancing",
                        # Re-mints a centered ±3% range every
                        # ``rebalance_interval`` rounds. On a CLMM
                        # (Whirlpool here) the periodic timer issues a
                        # WITHDRAW; the bootstrap branch re-mints on
                        # the next decide tick centered on the new
                        # spot — visible as a sawtooth in the Total
                        # LP Deposits chart's agent-L band, in
                        # contrast with the flat ``lp-passive`` line.
                        # Tight ±3% range keeps fee density high and
                        # makes the rebalancing benefit obvious.
                        "params": {
                            "collateral": "USDC",
                            "range_mode": "symmetric_pct",
                            "range_width_pct": 0.03,
                            "rebalance_interval": 25,
                            "rebalance_on_exit": False,
                        },
                        "initial_balances": {"USDC": 2_000_000_000, "SOL": 10_000_000_000},
                    },
                    {
                        "type": "jito_searcher",
                        "agent_id": "searcher-1",
                        "params": {
                            "strategies": ["sandwich"],
                            "tip_curve": {
                                "kind": "linear",
                                "slope_micro_lamports_per_ev": 0.05,
                            },
                            "min_ev_to_submit_lamports": 3_000_000,
                            "tip_account": "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
                            "max_bundle_size": 5,
                            "priority_fee_percentile_target": 75,
                            "alt_ids": ["alt-whirlpool-sol-usdc"],
                        },
                        "initial_balances": {
                            "USDC": 25_000_000,
                            "SOL": 25_000_000,
                        },
                    },
                ],
                "execution": {
                    "type": "solana_like",
                    "ordering": {"type": "priority"},
                    "gas_model": {"type": "compute_unit", "params": {}},
                    "params": {
                        "cost_token": "USDC",
                        "visible_roles": ["jito_searcher"],
                        "compute_budget": {
                            "per_slot": 1_200_000,
                            "per_tx": 1_400_000,
                            "per_writable_account": 600_000,
                        },
                        "submission_priors": {
                            "jito_relayer_landing_prob_baseline": 1.0,
                        },
                        "priority_fee_market": {
                            "window_slots": 150,
                            "ewma_half_life_slots": 30,
                            "floor_micro_lamports": 1,
                            "update_event_threshold": 0.001,
                            # PRD US-001 line 58: 200-slot pre-roll so the
                            # priority-fee distribution is non-degenerate
                            # before slot 0. Seeds the pool's write-lock
                            # account so the JitoSearcher gets sensible
                            # percentiles for its first sandwich attempt
                            # rather than the floor (1 micro-lamport)
                            # quote it would see otherwise.
                            "pre_roll": {
                                "slots": 200,
                                "accounts": ["HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"],
                                "cu_price_min": 1_000,
                                "cu_price_max": 50_000,
                                "observations_per_slot": 2,
                                "seed": 1337,
                            },
                        },
                        "bundle_auction": {
                            "max_bundles_per_slot": 5,
                            "jito_stake_pool_share": 0.05,
                            # FIX-020: Jito tip-quote prior fit on real
                            # mainnet bundles captured 2026-05-05. Lets
                            # the auction quote a sensible cohort tip on
                            # the first sandwich attempt rather than the
                            # floor for ~150 slots.
                            "tip_quote_curve_path": "solana-plans/calibration/jito_tip_curves.yaml",
                        },
                    },
                },
                "alts": [
                    {
                        # Real ALT-style entries: every account a swap touches
                        # on the canonical SOL/USDC Whirlpool, plus the system
                        # programs the bundle's instructions reference. These
                        # are the ~30 accounts a real sandwich bundle on this
                        # pool would compress into the lookup table.
                        "id": "alt-whirlpool-sol-usdc",
                        "entries": [
                            # Pool + vaults
                            "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
                            "3YQm7ujtXWJU2e9jhp2QGHpnn1ShXn12QjvzMvDgabpX",
                            "2JTw1fE2wz1SymWUQ7UqpVtrTuKjcd6mWwYwUJUCh2rq",
                            # Tick arrays around the active tick (captured slot 417595698)
                            "A2W6hiA2nf16iqtbZt9vX8FJbiXjv3DBUG3DgTja61HT",
                            "CEstjhG1v4nUgvGDyFruYEbJ18X8XeN4sX1WFCLt4D5c",
                            "HoDhUt77EotPNLUfJuvCCLbmpiM1JR6WLqWxeDPR1xvK",
                            # Whirlpools config
                            "2LecshUwdy9xi7meFgHtFJQNSKk4KdTrcpvaB56dP2NQ",
                            # Token mints
                            "So11111111111111111111111111111111111111112",
                            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                            # System / token / compute-budget / ATA
                            "11111111111111111111111111111111",
                            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                            "ComputeBudget111111111111111111111111111111",
                            "SysvarRent111111111111111111111111111111111",
                            "SysvarC1ock11111111111111111111111111111111",
                            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
                            # Jito tip account + validator-side programs
                            "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
                            "Stake11111111111111111111111111111111111111",
                            "Vote111111111111111111111111111111111111111",
                        ],
                    },
                ],
                "num_rounds": 500,
                "snapshot_interval": 1,
                "seed": 1337,
            },
            "editable_fields": [
                "market.params.corpus_slot",
                "market.params.pool_pubkey",
                "market.params.initial_liquidity",
                # Noise-trader sizing (agents 0..3): the macro flow knob.
                # The captured Whirlpool only has ~$130K USDC vault depth,
                # so without these the price chart stays inside ±0.5%
                # regardless of victim/searcher tuning.
                "agents[0].params.frequency",
                "agents[0].params.trade_max",
                "agents[1].params.frequency",
                "agents[1].params.trade_max",
                "agents[2].params.frequency",
                "agents[2].params.trade_max",
                "agents[3].params.frequency",
                "agents[3].params.trade_max",
                # Victim flow (visible-mempool swaps the searcher targets).
                "agents[4].params.frequency",
                "agents[4].params.amount_max",
                "agents[5].params.frequency",
                "agents[5].params.amount_max",
                # Range-aware LPs: width controls in-range fraction vs.
                # fee density; rebalance_interval (rebalancing variant
                # only) controls how often the LP re-mints centered on
                # the new spot. agents[7]=lp-passive (±10%, no
                # rebalance), agents[8]=lp-rebalancing (±3%, periodic
                # WITHDRAW + bootstrap re-mint).
                "agents[7].params.range_width_pct",
                "agents[7].params.rebalance_on_exit",
                "agents[8].params.range_width_pct",
                "agents[8].params.rebalance_interval",
                # Searcher knobs (now at agents[9] after the LP split).
                "agents[9].params.tip_curve.slope_micro_lamports_per_ev",
                "agents[9].params.min_ev_to_submit_lamports",
                "execution.params.compute_budget.per_slot",
                "execution.ordering.type",
                "num_rounds",
            ],
            "recommended_metrics": [
                "final_yes_price",
                "price_dislocation",
                "num_rounds_executed",
                "stopped_early",
                # Range-aware LP metrics: surface both pool-wide averages
                # and the per-LP ``<metric>:lp-passive`` /
                # ``<metric>:lp-rebalancing`` variants so the demo can
                # contrast a deposit-and-hold LP against a periodically-
                # rebalancing one on the same Whirlpool fork.
                "lp_in_range_fraction",
                "lp_in_range_fraction:lp-passive",
                "lp_in_range_fraction:lp-rebalancing",
                "range_il",
                "range_il:lp-passive",
                "range_il:lp-rebalancing",
                "fees_vs_il_breakeven",
                "fees_vs_il_breakeven:lp-passive",
                "fees_vs_il_breakeven:lp-rebalancing",
                # LP fees normalized by raw liquidity — the purpose-fit
                # metric for the protocol-design counterfactual: lowering
                # the fee tier should drop this on the second run.
                "lp_fees_per_liquidity",
                "lp_fees_per_liquidity:lp-passive",
                "lp_fees_per_liquidity:lp-rebalancing",
            ],
            "synthetic_mode": False,
            "synthetic_math_model": None,
            "featured": True,
            "non_transferable_conclusions": [
                "Pool depth, liquidity distribution, and starting price reflect a single "
                "captured mainnet slot; the snapshot is point-in-time and ages as mainnet moves. "
                "Bundle-landing-rate priors (1.5 / 1.11) remain uncalibrated until Phase 2.4 ships, "
                "so absolute landing-rate numbers should be read as 'mechanism behaviour' rather "
                "than 'mainnet projection' — the JitoSearcher metrics carry a synthetic_priors "
                "marker. Re-snapshot via tools/cache_corpus_slot.py + tools/snapshotter to refresh "
                "the underlying Whirlpool state."
            ],
            "synthetic_blockers": [
                "2.4 calibration (landing-rate priors)",
            ],
        },
    ]
