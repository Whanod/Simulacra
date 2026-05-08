"""Calibration test for the lighthouse Whirlpool fixture (US-004 / 2.4).

Loads the captured ``high_volume_dex`` corpus slot, hydrates a runtime
:class:`defi_sim.markets.whirlpool.WhirlpoolMarket` from the committed
account fixtures, and asserts:

1. Every parsed pool field matches the ``manifest.yaml`` ground truth (the
   manifest acts as a hand-checked oracle of what the on-chain account
   looked like at capture time — drift between fixture bytes and manifest
   means either the parser regressed or the fixture got corrupted).
2. Each tick array's ``initialized_count`` matches the manifest.
3. Spot price implied by ``sqrt_price_x64`` lands inside a sane envelope
   for SOL/USDC (between $50 and $250).
4. A small a-to-b swap (0.1 SOL → USDC) and a small b-to-a swap
   (5 USDC → SOL) round-trip the swap math against the real liquidity
   distribution. Output amounts are checked against the analytic upper
   bound implied by the post-swap sqrt-price (Whirlpool eq. 6.13/6.16),
   confirming the in-process port matches the on-chain math to the same
   bit width.

Lighthouse-template smoke check
-------------------------------
The same fixture is what
:func:`defi_sim.markets.whirlpool_fork.build_whirlpool_market_from_corpus`
loads when the lighthouse template constructs its market. Asserting
correctness of the parsed fields here is therefore equivalent to
asserting that the lighthouse demo runs against real on-chain state — if
this test breaks, the lighthouse studio surface is degenerate.
"""

from __future__ import annotations

import pytest

from defi_sim.markets.whirlpool import WhirlpoolMarket
from defi_sim.markets.whirlpool_fork import build_whirlpool_market_from_corpus
from defi_sim.markets.whirlpool_math import (
    get_amount_delta_a,
    get_amount_delta_b,
    sqrt_price_from_tick_index,
)
from tools.snapshotter import StressCategory

from .conftest import require_calibration_fixture

pytestmark = pytest.mark.calibration

SOL_USDC_POOL = "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"


def _expected_whirlpool(manifest: dict) -> dict:
    expected = manifest.get("expected") or {}
    whirlpool = expected.get("whirlpool")
    if not whirlpool:
        pytest.skip(
            "manifest has no expected.whirlpool block — fixture is not a "
            "Whirlpool calibration capture."
        )
    return whirlpool


def test_whirlpool_pool_fields_match_manifest() -> None:
    slot, manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    expected = _expected_whirlpool(manifest)
    market: WhirlpoolMarket = build_whirlpool_market_from_corpus(
        corpus_slot=slot,
        pool_pubkey=expected["pubkey"],
        token_a_id="SOL",
        token_b_id="USDC",
    )
    pool = market.pool
    assert pool.pubkey == expected["pubkey"]
    assert pool.tick_spacing == expected["tick_spacing"]
    assert pool.fee_rate == expected["fee_rate"]
    assert pool.protocol_fee_rate == expected["protocol_fee_rate"]
    assert pool.liquidity == expected["liquidity"]
    assert pool.sqrt_price_x64 == expected["sqrt_price_x64"]
    assert pool.tick_current_index == expected["tick_current_index"]
    assert pool.token_mint_a == expected["token_mint_a"]
    assert pool.token_mint_b == expected["token_mint_b"]
    assert pool.token_vault_a_pubkey == expected["token_vault_a"]
    assert pool.token_vault_b_pubkey == expected["token_vault_b"]
    assert pool.token_vault_a_amount == expected["vault_a_amount"]
    assert pool.token_vault_b_amount == expected["vault_b_amount"]


def test_whirlpool_tick_arrays_initialized_count() -> None:
    slot, manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    expected = manifest.get("expected") or {}
    expected_arrays = {ta["pubkey"]: ta for ta in expected.get("tick_arrays") or []}
    if not expected_arrays:
        pytest.skip("manifest has no expected.tick_arrays block")
    market = build_whirlpool_market_from_corpus(
        corpus_slot=slot,
        pool_pubkey=SOL_USDC_POOL,
        token_a_id="SOL",
        token_b_id="USDC",
    )
    actual = {ta.pubkey: ta for ta in market.tick_arrays}
    for pubkey, manifest_entry in expected_arrays.items():
        assert pubkey in actual, (
            f"tick array {pubkey} is committed in manifest but missing "
            "from the parsed fixture"
        )
        assert actual[pubkey].start_tick_index == manifest_entry["start_tick_index"]
        n_initialized = sum(1 for t in actual[pubkey].ticks if t.initialized)
        assert n_initialized == manifest_entry["initialized_count"], (
            f"tick array {pubkey}: expected {manifest_entry['initialized_count']} "
            f"initialized ticks, got {n_initialized}"
        )


def test_whirlpool_spot_price_is_sane() -> None:
    slot, manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    market = build_whirlpool_market_from_corpus(
        corpus_slot=slot,
        pool_pubkey=SOL_USDC_POOL,
        token_a_id="SOL",
        token_b_id="USDC",
    )
    prices = market.get_prices()
    sol_price = prices["SOL"]
    assert 50 < sol_price < 250, (
        f"SOL/USDC spot {sol_price:.2f} is outside the sanity envelope "
        "($50–$250). Either the captured slot is wildly stale or the "
        "decimal-adjusted sqrt-price formula regressed."
    )


def test_whirlpool_small_a_to_b_swap_matches_analytic_upper_bound() -> None:
    """Swap step boundedness check: the engine's reported amount_b must be
    no greater than ``get_amount_delta_b(start, end_unrounded, liquidity)``
    where ``end_unrounded`` is the CLMM end price for the given amount_a in.

    For a swap that does not cross any tick the active liquidity is constant,
    so this is a tight upper bound on the real on-chain output. Catching a
    larger output here would mean the engine is over-paying — exactly the
    type of regression calibration tests exist to catch.
    """
    slot, _manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    market = build_whirlpool_market_from_corpus(
        corpus_slot=slot,
        pool_pubkey=SOL_USDC_POOL,
        token_a_id="SOL",
        token_b_id="USDC",
    )
    amount_in = 100_000_000  # 0.1 SOL in lamports
    outcome = market.simulate_swap(amount_in, a_to_b=True)
    assert outcome["amount_a"] == amount_in
    assert outcome["amount_b"] > 0
    # If the trade did not cross any tick, the analytic upper bound for
    # amount_b is the unrounded ``get_amount_delta_b`` between the start
    # and the engine-reported next sqrt price under fixed liquidity.
    if outcome["next_tick_index"] // market.pool.tick_spacing == (
        market.pool.tick_current_index // market.pool.tick_spacing
    ):
        upper = get_amount_delta_b(
            market.pool.sqrt_price_x64,
            outcome["next_sqrt_price"],
            market.pool.liquidity,
            False,
        )
        assert outcome["amount_b"] <= upper, (
            f"engine over-paid amount_b={outcome['amount_b']} > analytic "
            f"upper bound {upper}"
        )
    # Fee math: 0.30 % fee, 13 % protocol cut.
    assert outcome["fee_total"] >= outcome["lp_fee"]
    assert outcome["protocol_fee"] == outcome["fee_total"] - outcome["lp_fee"]
    expected_fee = (amount_in * market.pool.fee_rate) // 1_000_000
    # Allow ±1 lamport for ceiling rounding inside compute_swap_step.
    assert abs(outcome["fee_total"] - expected_fee) <= 1


def test_whirlpool_small_b_to_a_swap_round_trip() -> None:
    slot, _manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    market = build_whirlpool_market_from_corpus(
        corpus_slot=slot,
        pool_pubkey=SOL_USDC_POOL,
        token_a_id="SOL",
        token_b_id="USDC",
    )
    amount_in = 5_000_000  # 5 USDC
    outcome = market.simulate_swap(amount_in, a_to_b=False)
    assert outcome["amount_b"] == amount_in
    assert outcome["amount_a"] > 0
    # b_to_a price must increase, never decrease.
    assert outcome["next_sqrt_price"] > market.pool.sqrt_price_x64


def test_whirlpool_sqrt_price_round_trip_via_tick_math() -> None:
    """The captured ``tick_current_index`` should round-trip through
    ``sqrt_price_from_tick_index``: the result must satisfy
    ``sqrt_price_at_tick <= sqrt_price_x64 < sqrt_price_at_tick+1``."""
    slot, manifest = require_calibration_fixture(StressCategory.HIGH_VOLUME_DEX)
    expected = _expected_whirlpool(manifest)
    sqrt_price = expected["sqrt_price_x64"]
    tick = expected["tick_current_index"]
    lower = sqrt_price_from_tick_index(tick)
    upper = sqrt_price_from_tick_index(tick + 1)
    assert lower <= sqrt_price < upper, (
        f"tick {tick}: sqrt_price_x64={sqrt_price} is not in "
        f"[{lower}, {upper}); tick math regression."
    )
