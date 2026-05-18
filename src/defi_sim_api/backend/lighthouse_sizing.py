"""Role-based agent sizing for the lighthouse Whirlpool template.

The lighthouse template carries a ``market.params.initial_liquidity`` slider
(target token-B vault depth in human units). ``whirlpool_fork.py`` already
rescales pool depth against that knob; this module rescales agent trade
sizes and balances by the same mechanism so flow tracks pool depth.

The sizing pass is opt-in via ``spec["lighthouse_sizing"] = True`` and only
fires for ``market.type == "whirlpool"`` specs. Each agent in the spec may
carry an optional ``"sizing_role"`` key that selects a row from
:data:`ROLE_FRACTIONS`; if absent the agent's ``"type"`` is used as the
key. Agents whose role is not in the table are passed through untouched.

Fractions are expressed in token-B human units (i.e. dollars on a
SOL/USDC pool). Token-A balances are derived from token-B-equivalent
fractions via the captured pool's spot price.
"""

from __future__ import annotations

from typing import Any, Mapping


# Fractions of the captured token-B vault depth — resolved at spec-load
# time against whatever pool is loaded, so the table tracks the snapshot
# automatically (currently the 4 bps SOL/USDC Whirlpool, ~$8M USDC vault
# depth). Adjust here, not in templates.py.
ROLE_FRACTIONS: dict[str, dict[str, float]] = {
    # Bidirectional macro flow. Trade range spans 0.08% to 4% of vault per
    # tick, so the pool sees a mix of in-tick and active-tick-edge swaps.
    "noise": {
        "trade_min": 0.0008,
        "trade_max": 0.04,
        "balance_b": 4.0,
        "balance_a_in_b": 4.0,
    },
    # Visible-mempool victims for the Jito searcher. "large" sits in the
    # 0.4–20% band so sandwiches have real EV; "small" in 0.04–1.2%
    # populates the floor of the priority-fee distribution.
    "swap_noise:large": {
        "amount_min": 0.004,
        "amount_max": 0.20,
        "balance_b": 8.0,
        "balance_a_in_b": 8.0,
    },
    "swap_noise:small": {
        "amount_min": 0.0004,
        "amount_max": 0.012,
        "balance_b": 4.0,
        "balance_a_in_b": 4.0,
    },
    "manipulator": {
        "budget": 0.40,
        "balance_b": 4.0,
    },
    "passive_lp": {
        "balance_b": 15.0,
        "balance_a_in_b": 15.0,
    },
    "jito_searcher": {
        "balance_b": 0.2,
        "balance_a_in_b": 0.2,
    },
}


def _spot_price_b_per_a_human(
    sqrt_price_x64: int,
    decimals_a: int,
    decimals_b: int,
) -> float:
    """Token-B human units per token-A human unit at the current sqrt price.

    Whirlpool stores ``sqrt_price`` in Q64.64 fixed-point with token-B/A in
    raw-unit terms. Converting to human-unit price-of-A-in-B requires
    squaring the sqrt and applying the decimals delta.
    """
    if sqrt_price_x64 <= 0:
        return 0.0
    sqrt_price = sqrt_price_x64 / float(1 << 64)
    raw_b_per_a = sqrt_price * sqrt_price
    return raw_b_per_a * (10 ** decimals_a) / (10 ** decimals_b)


def apply_lighthouse_sizing(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve agent sizing fractions into raw integer fields.

    Returns a new dict; the input mapping is not mutated.

    Triggers on any captured Whirlpool spec — i.e. ``market.type ==
    "whirlpool"`` with both ``corpus_slot`` and ``pool_pubkey`` set. The
    explicit ``spec["lighthouse_sizing"]`` flag is now an opt-OUT only:
    set it to ``False`` to skip rescaling. Inferring the trigger from
    structural conditions instead of a flag avoids the class of bugs
    where the frontend's ``specToApi`` (or any other serializer) drops
    an unknown field on the way to the API and silently disables the
    rescaling pass — which left agent trade caps at their unscaled raw
    defaults and produced empty CLMM-LP metrics.
    """
    spec_out: dict[str, Any] = dict(spec)
    if spec_out.get("lighthouse_sizing") is False:
        return spec_out
    market = spec_out.get("market") or {}
    if market.get("type") != "whirlpool":
        return spec_out
    params = market.get("params") or {}
    if "corpus_slot" not in params or "pool_pubkey" not in params:
        return spec_out

    # Load the snapshot to read post-scale vault depth, decimals, and spot.
    # The fork loader is the only authority that knows how the
    # ``initial_liquidity`` slider rescales the captured pool.
    from defi_sim.markets.whirlpool_fork import build_whirlpool_market_from_corpus

    market_obj = build_whirlpool_market_from_corpus(
        corpus_slot=int(params["corpus_slot"]),
        pool_pubkey=str(params["pool_pubkey"]),
        token_a_id=str(params.get("token_a_id", "")),
        token_b_id=str(params.get("token_b_id", "")),
        token_a_symbol=str(params.get("token_a_symbol", "")),
        token_b_symbol=str(params.get("token_b_symbol", "")),
        initial_liquidity=params.get("initial_liquidity"),
    )
    pool = market_obj._pool
    decimals_a = pool.token_decimals_a
    decimals_b = pool.token_decimals_b
    depth_b_raw = int(pool.token_vault_b_amount)
    if depth_b_raw <= 0:
        return spec_out
    depth_b_human = depth_b_raw / (10 ** decimals_b)
    spot_b_per_a_human = _spot_price_b_per_a_human(
        pool.sqrt_price_x64, decimals_a, decimals_b
    )

    # Prefer the symbol as the balance-key token id, matching the engine
    # side ``_resolve_token``. ``params["token_a_id"]`` may be a mint
    # pubkey (the Builder's corpus dropdown stores mints there), and
    # writing balances under that key would mismatch the symbol-keyed
    # token id the engine registers — every swap would silently fail
    # the agent balance lookup.
    token_a_id = str(
        params.get("token_a_symbol") or params.get("token_a_id") or pool.token_mint_a
    )
    token_b_id = str(
        params.get("token_b_symbol") or params.get("token_b_id") or pool.token_mint_b
    )

    new_agents: list[dict[str, Any]] = []
    for agent in spec_out.get("agents", []) or []:
        a = dict(agent)
        sizing_role = a.get("sizing_role") or a.get("type", "")
        fractions = ROLE_FRACTIONS.get(str(sizing_role))
        if fractions is None:
            new_agents.append(a)
            continue

        params_dict = dict(a.get("params", {}))
        for field in ("trade_min", "trade_max", "amount_min", "amount_max", "budget"):
            if field in fractions:
                params_dict[field] = int(depth_b_raw * fractions[field])
        a["params"] = params_dict

        balances = dict(a.get("initial_balances", {}))
        if "balance_b" in fractions:
            balances[token_b_id] = int(depth_b_raw * fractions["balance_b"])
        if "balance_a_in_b" in fractions and spot_b_per_a_human > 0:
            token_a_human = depth_b_human * fractions["balance_a_in_b"] / spot_b_per_a_human
            balances[token_a_id] = int(token_a_human * (10 ** decimals_a))
        a["initial_balances"] = balances

        new_agents.append(a)

    spec_out["agents"] = new_agents
    return spec_out
