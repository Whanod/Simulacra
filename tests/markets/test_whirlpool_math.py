"""Pin the Whirlpool math port against the Rust reference unit tests.

Every assertion here is a literal translation of a unit test in
``programs/whirlpool/src/math/{tick_math,token_math,swap_math}.rs`` from
the upstream orca-so/whirlpools repository. If the port drifts from the
on-chain implementation (rounding mode, fixed-point shift, fee-rate
multiplier, etc.), this file fails — and so does the lighthouse demo's
calibrated behaviour.
"""

from __future__ import annotations

import pytest

from defi_sim.markets.whirlpool_math import (
    MAX_SQRT_PRICE_X64,
    MAX_TICK_INDEX,
    MIN_SQRT_PRICE_X64,
    MIN_TICK_INDEX,
    Q64,
    compute_swap_step,
    get_amount_delta_a,
    get_amount_delta_b,
    sqrt_price_from_tick_index,
    tick_index_from_sqrt_price,
)


# --- tick_math.rs::test_exact_bit_values + boundary checks --------------------

@pytest.mark.parametrize(
    "tick,expected_pos,expected_neg",
    [
        (0, 18446744073709551616, 18446744073709551616),
        (1, 18447666387855959850, 18445821805675392311),
        (2, 18448588748116922571, 18444899583751176498),
        (4, 18450433606991734263, 18443055278223354162),
        (64, 18505865242158250041, 18387811781193591352),
        (256, 18684368066214940582, 18212142134806087854),
        (4096, 22639080592224303007, 15030750278693429944),
    ],
)
def test_sqrt_price_from_tick_exact_bit_values(
    tick: int, expected_pos: int, expected_neg: int
) -> None:
    assert sqrt_price_from_tick_index(tick) == expected_pos
    assert sqrt_price_from_tick_index(-tick) == expected_neg


def test_sqrt_price_from_tick_at_bounds() -> None:
    assert sqrt_price_from_tick_index(MAX_TICK_INDEX) == MAX_SQRT_PRICE_X64
    assert sqrt_price_from_tick_index(MIN_TICK_INDEX) == MIN_SQRT_PRICE_X64


@pytest.mark.parametrize(
    "tick", [0, 1, -1, 100, -100, 12345, -12345, 100_000, -100_000, MAX_TICK_INDEX - 1, MIN_TICK_INDEX + 1]
)
def test_tick_sqrt_price_round_trip(tick: int) -> None:
    sp = sqrt_price_from_tick_index(tick)
    assert tick_index_from_sqrt_price(sp) == tick


# --- token_math.rs::test_get_amount_delta_ok ---------------------------------

def test_amount_delta_basic() -> None:
    assert get_amount_delta_a(4 << 64, 2 << 64, 4, True) == 1
    assert get_amount_delta_a(4 << 64, 2 << 64, 4, False) == 1
    assert get_amount_delta_b(4 << 64, 2 << 64, 4, True) == 8
    assert get_amount_delta_b(4 << 64, 2 << 64, 4, False) == 8


def test_amount_delta_zero_price_diff() -> None:
    assert get_amount_delta_a(4 << 64, 4 << 64, 4, True) == 0
    assert get_amount_delta_b(4 << 64, 4 << 64, 4, False) == 0


# --- swap_math.rs::test_compute_swap (TWO_PCT = 20000 = 2 %) -----------------

TWO_PCT = 20_000


def _step(*args, **kwargs):
    return compute_swap_step(*args, **kwargs)


def test_compute_swap_a_to_b_input_max() -> None:
    step = _step(1000, TWO_PCT, 1296, 9 << 64, 4 << 64, True, True)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        180,
        6480,
        4 << 64,
        4,
    )


def test_compute_swap_a_to_b_input_max_1pct_fee() -> None:
    step = _step(1000, TWO_PCT // 2, 1296, 9 << 64, 4 << 64, True, True)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        180,
        6480,
        4 << 64,
        2,
    )


def test_compute_swap_a_to_b_output() -> None:
    step = _step(4723, TWO_PCT, 1296, 9 << 64, 4 << 64, False, True)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        98,
        4723,
        98795409425631171116,
        2,
    )


def test_compute_swap_a_to_b_output_max() -> None:
    step = _step(10000, TWO_PCT, 1296, 9 << 64, 4 << 64, False, True)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        180,
        6480,
        4 << 64,
        4,
    )


def test_compute_swap_b_to_a_input() -> None:
    step = _step(2000, TWO_PCT, 1296, 9 << 64, 16 << 64, True, False)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        1960,
        20,
        193918550355107200012,
        40,
    )


def test_compute_swap_b_to_a_input_max() -> None:
    step = _step(20000, TWO_PCT, 1296, 9 << 64, 16 << 64, True, False)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        9072,
        63,
        16 << 64,
        186,
    )


def test_compute_swap_b_to_a_output() -> None:
    step = _step(20, TWO_PCT, 1296, 9 << 64, 16 << 64, False, False)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        1882,
        20,
        192798228383286926568,
        39,
    )


def test_compute_swap_b_to_a_output_max() -> None:
    step = _step(80, TWO_PCT, 1296, 9 << 64, 16 << 64, False, False)
    assert (step.amount_in, step.amount_out, step.next_sqrt_price, step.fee_amount) == (
        9072,
        63,
        16 << 64,
        186,
    )


@pytest.mark.parametrize("a_to_b", [True, False])
@pytest.mark.parametrize("amount_specified_is_input", [True, False])
def test_compute_swap_zero_amount_returns_no_change(
    a_to_b: bool, amount_specified_is_input: bool
) -> None:
    target = (4 << 64) if a_to_b else (16 << 64)
    step = _step(0, TWO_PCT, 1296, 9 << 64, target, amount_specified_is_input, a_to_b)
    assert step.amount_in == 0
    assert step.amount_out == 0
    assert step.fee_amount == 0
    # next_sqrt_price collapses to current when no amount is exchanged.
    assert step.next_sqrt_price == 9 << 64


@pytest.mark.parametrize("a_to_b", [True, False])
def test_compute_swap_zero_liquidity_skips_to_target(a_to_b: bool) -> None:
    target = (4 << 64) if a_to_b else (16 << 64)
    step = _step(100, TWO_PCT, 0, 9 << 64, target, True, a_to_b)
    assert step.amount_in == 0
    assert step.amount_out == 0
    assert step.fee_amount == 0
    assert step.next_sqrt_price == target
