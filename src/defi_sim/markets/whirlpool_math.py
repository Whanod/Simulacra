"""Whirlpool CLMM math primitives — port of orca-so/whirlpools.

Translated from `programs/whirlpool/src/math/{tick_math,token_math,swap_math}.rs`
at the orca-so/whirlpools tip (commit-pinned via the docstring of
``WhirlpoolMarket``). Python uses unbounded ints, so the u64/u128 saturation
checks in the Rust reference become explicit ``ValueError`` raises here —
the math is otherwise bit-identical, including all rounding modes.

The functions are exposed as module-level pure routines so the test suite
can hit the ladder of inputs the Rust property tests cover, and the market
class composes them into a swap iteration.

Q64.64 fixed-point convention:
* ``sqrt_price_x64 = sqrt(price_b_per_a) * 2**64`` — Whirlpool's canonical
  square-root price encoding.
* ``fee_rate`` is in hundredths of a basis point (e.g., 3000 = 0.30 %).
* ``protocol_fee_rate`` is in basis points of the LP fee (e.g., 1300 = 13 %).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "FEE_RATE_DENOMINATOR",
    "MAX_SQRT_PRICE_X64",
    "MAX_TICK_INDEX",
    "MIN_SQRT_PRICE_X64",
    "MIN_TICK_INDEX",
    "PROTOCOL_FEE_DENOMINATOR",
    "Q64",
    "Q64_MASK",
    "SwapStep",
    "compute_swap_step",
    "div_round_up",
    "get_amount_delta_a",
    "get_amount_delta_b",
    "get_next_sqrt_price",
    "get_next_sqrt_price_from_a_round_up",
    "get_next_sqrt_price_from_b_round_down",
    "increasing_price_order",
    "sqrt_price_from_tick_index",
    "tick_index_from_sqrt_price",
]

Q64 = 1 << 64
Q64_MASK = Q64 - 1
U64_MAX = (1 << 64) - 1
U128_MAX = (1 << 128) - 1

MIN_TICK_INDEX = -443636
MAX_TICK_INDEX = 443636
MIN_SQRT_PRICE_X64 = 4295048016
MAX_SQRT_PRICE_X64 = 79226673515401279992447579055

FEE_RATE_DENOMINATOR = 1_000_000
PROTOCOL_FEE_DENOMINATOR = 10_000


def _checked_u128(value: int, *, where: str) -> int:
    if value < 0 or value > U128_MAX:
        raise ValueError(f"{where}: u128 overflow ({value})")
    return value


def _checked_u64(value: int, *, where: str) -> int:
    if value < 0 or value > U64_MAX:
        raise ValueError(f"{where}: u64 overflow ({value})")
    return value


def div_round_up(numerator: int, denominator: int) -> int:
    """Ceiling division for non-negative integers."""
    if denominator == 0:
        raise ZeroDivisionError("div_round_up: denominator must be non-zero")
    quotient, remainder = divmod(numerator, denominator)
    if remainder:
        return quotient + 1
    return quotient


def _div_round_up_if(numerator: int, denominator: int, round_up: bool) -> int:
    return div_round_up(numerator, denominator) if round_up else (numerator // denominator)


def increasing_price_order(p0: int, p1: int) -> tuple[int, int]:
    return (p1, p0) if p0 > p1 else (p0, p1)


# ---------------------------------------------------------------------------
# tick_math.rs port
# ---------------------------------------------------------------------------

# `mul_shift_96` in Rust uses 256-bit math. Python ints are unbounded so the
# multiplication is exact; the >> 96 is equivalent to the U256 shift.
def _mul_shift_96(n0: int, n1: int) -> int:
    return (n0 * n1) >> 96


_POSITIVE_BASE_EVEN = 79228162514264337593543950336  # 2^96
_POSITIVE_BASE_ODD = 79232123823359799118286999567  # sqrt(1.0001) << 96

_POSITIVE_TICK_MULTIPLIERS: tuple[tuple[int, int], ...] = (
    (2, 79236085330515764027303304731),
    (4, 79244008939048815603706035061),
    (8, 79259858533276714757314932305),
    (16, 79291567232598584799939703904),
    (32, 79355022692464371645785046466),
    (64, 79482085999252804386437311141),
    (128, 79736823300114093921829183326),
    (256, 80248749790819932309965073892),
    (512, 81282483887344747381513967011),
    (1024, 83390072131320151908154831281),
    (2048, 87770609709833776024991924138),
    (4096, 97234110755111693312479820773),
    (8192, 119332217159966728226237229890),
    (16384, 179736315981702064433883588727),
    (32768, 407748233172238350107850275304),
    (65536, 2098478828474011932436660412517),
    (131072, 55581415166113811149459800483533),
    (262144, 38992368544603139932233054999993551),
)

_NEGATIVE_BASE_EVEN = 18446744073709551616  # 2^64
_NEGATIVE_BASE_ODD = 18445821805675392311
_NEGATIVE_TICK_MULTIPLIERS: tuple[tuple[int, int], ...] = (
    (2, 18444899583751176498),
    (4, 18443055278223354162),
    (8, 18439367220385604838),
    (16, 18431993317065449817),
    (32, 18417254355718160513),
    (64, 18387811781193591352),
    (128, 18329067761203520168),
    (256, 18212142134806087854),
    (512, 17980523815641551639),
    (1024, 17526086738831147013),
    (2048, 16651378430235024244),
    (4096, 15030750278693429944),
    (8192, 12247334978882834399),
    (16384, 8131365268884726200),
    (32768, 3584323654723342297),
    (65536, 696457651847595233),
    (131072, 26294789957452057),
    (262144, 37481735321082),
)

_LOG_B_2_X32 = 59543866431248
_BIT_PRECISION = 14
_LOG_B_P_ERR_MARGIN_LOWER_X64 = 184467440737095516
_LOG_B_P_ERR_MARGIN_UPPER_X64 = 15793534762490258745


def _signed_i128_from_unsigned(value: int) -> int:
    """Reinterpret a non-negative two's-complement encoded i128 as a signed int."""
    if value & (1 << 127):
        return value - (1 << 128)
    return value


def sqrt_price_from_tick_index(tick: int) -> int:
    """Q64.64 sqrt-price for an integer tick index. Mirrors Whirlpool exactly."""
    if not (-2 * MAX_TICK_INDEX <= tick <= 2 * MAX_TICK_INDEX):
        raise ValueError(f"tick {tick} out of bounds for sqrt_price_from_tick_index")

    if tick >= 0:
        ratio = _POSITIVE_BASE_ODD if tick & 1 else _POSITIVE_BASE_EVEN
        for bit, mult in _POSITIVE_TICK_MULTIPLIERS:
            if tick & bit:
                ratio = _mul_shift_96(ratio, mult)
        return ratio >> 32

    abs_tick = -tick
    ratio = _NEGATIVE_BASE_ODD if abs_tick & 1 else _NEGATIVE_BASE_EVEN
    for bit, mult in _NEGATIVE_TICK_MULTIPLIERS:
        if abs_tick & bit:
            ratio = (ratio * mult) >> 64
    return ratio


def tick_index_from_sqrt_price(sqrt_price_x64: int) -> int:
    """Inverse of :func:`sqrt_price_from_tick_index`."""
    if sqrt_price_x64 < 1:
        raise ValueError("sqrt_price_x64 must be positive")
    msb = sqrt_price_x64.bit_length() - 1
    log2p_integer_x32 = (msb - 64) << 32

    bit = 0x8000_0000_0000_0000
    precision = 0
    log2p_fraction_x64 = 0
    if msb >= 64:
        r = sqrt_price_x64 >> (msb - 63)
    else:
        r = sqrt_price_x64 << (63 - msb)

    while bit > 0 and precision < _BIT_PRECISION:
        r *= r
        is_r_more_than_two = r >> 127
        r >>= 63 + is_r_more_than_two
        log2p_fraction_x64 += bit * is_r_more_than_two
        bit >>= 1
        precision += 1

    log2p_fraction_x32 = log2p_fraction_x64 >> 32
    log2p_x32 = log2p_integer_x32 + log2p_fraction_x32
    logbp_x64 = log2p_x32 * _LOG_B_2_X32

    tick_low = (logbp_x64 - _LOG_B_P_ERR_MARGIN_LOWER_X64) >> 64
    tick_high = (logbp_x64 + _LOG_B_P_ERR_MARGIN_UPPER_X64) >> 64

    if tick_low == tick_high:
        return tick_low
    if sqrt_price_from_tick_index(tick_high) <= sqrt_price_x64:
        return tick_high
    return tick_low


# ---------------------------------------------------------------------------
# token_math.rs port
# ---------------------------------------------------------------------------


def get_amount_delta_a(
    sqrt_price_0: int, sqrt_price_1: int, liquidity: int, round_up: bool
) -> int:
    """Token A delta between two sqrt prices for a given liquidity (Whirlpool eq. 6.16)."""
    lower, upper = increasing_price_order(sqrt_price_0, sqrt_price_1)
    diff = upper - lower
    numerator = liquidity * diff << 64
    denominator = upper * lower
    if denominator == 0:
        return 0
    quotient, remainder = divmod(numerator, denominator)
    if round_up and remainder:
        quotient += 1
    if quotient > U64_MAX:
        raise ValueError("get_amount_delta_a: u64 overflow")
    return quotient


def get_amount_delta_b(
    sqrt_price_0: int, sqrt_price_1: int, liquidity: int, round_up: bool
) -> int:
    """Token B delta between two sqrt prices for a given liquidity (Whirlpool eq. 6.14)."""
    lower, upper = increasing_price_order(sqrt_price_0, sqrt_price_1)
    diff = upper - lower
    if liquidity == 0 or diff == 0:
        return 0
    p = liquidity * diff
    result = p >> 64
    should_round = round_up and (p & Q64_MASK) > 0
    if should_round:
        result += 1
    if result > U64_MAX:
        raise ValueError("get_amount_delta_b: u64 overflow")
    return result


def _try_get_amount_delta_a_exceeds(
    sqrt_price_0: int, sqrt_price_1: int, liquidity: int, round_up: bool
) -> tuple[int | None, bool]:
    """Mirror of try_get_amount_delta_a: returns (value, exceeds)."""
    try:
        return get_amount_delta_a(sqrt_price_0, sqrt_price_1, liquidity, round_up), False
    except ValueError:
        return None, True


def _try_get_amount_delta_b_exceeds(
    sqrt_price_0: int, sqrt_price_1: int, liquidity: int, round_up: bool
) -> tuple[int | None, bool]:
    try:
        return get_amount_delta_b(sqrt_price_0, sqrt_price_1, liquidity, round_up), False
    except ValueError:
        return None, True


def get_next_sqrt_price_from_a_round_up(
    sqrt_price: int, liquidity: int, amount: int, amount_specified_is_input: bool
) -> int:
    """Whirlpool eq. 6.15: next sqrt-price after adding/removing token A."""
    if amount == 0:
        return sqrt_price
    product = sqrt_price * amount
    numerator = liquidity * sqrt_price << 64
    liquidity_shift_left = liquidity << 64
    if not amount_specified_is_input and liquidity_shift_left <= product:
        raise ValueError("get_next_sqrt_price_from_a_round_up: divide-by-zero (liquidity ≤ product)")
    if amount_specified_is_input:
        denominator = liquidity_shift_left + product
    else:
        denominator = liquidity_shift_left - product
    price = div_round_up(numerator, denominator)
    if price < MIN_SQRT_PRICE_X64:
        raise ValueError("get_next_sqrt_price_from_a_round_up: sqrt-price below MIN")
    if price > MAX_SQRT_PRICE_X64:
        raise ValueError("get_next_sqrt_price_from_a_round_up: sqrt-price above MAX")
    return price


def get_next_sqrt_price_from_b_round_down(
    sqrt_price: int, liquidity: int, amount: int, amount_specified_is_input: bool
) -> int:
    """Whirlpool eq. 6.13: next sqrt-price after adding/removing token B."""
    amount_x64 = amount << 64
    delta = _div_round_up_if(amount_x64, liquidity, not amount_specified_is_input)
    if amount_specified_is_input:
        result = sqrt_price + delta
    else:
        result = sqrt_price - delta
    if result < 0 or result > U128_MAX:
        raise ValueError("get_next_sqrt_price_from_b_round_down: sqrt-price out of bounds")
    return result


def get_next_sqrt_price(
    sqrt_price: int,
    liquidity: int,
    amount: int,
    amount_specified_is_input: bool,
    a_to_b: bool,
) -> int:
    """Dispatch to the A- or B-side ``get_next_sqrt_price`` helper."""
    if amount_specified_is_input == a_to_b:
        return get_next_sqrt_price_from_a_round_up(
            sqrt_price, liquidity, amount, amount_specified_is_input
        )
    return get_next_sqrt_price_from_b_round_down(
        sqrt_price, liquidity, amount, amount_specified_is_input
    )


# ---------------------------------------------------------------------------
# swap_math.rs port
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwapStep:
    amount_in: int
    amount_out: int
    next_sqrt_price: int
    fee_amount: int


def _get_amount_fixed_delta(
    sqrt_price_current: int,
    sqrt_price_target: int,
    liquidity: int,
    amount_specified_is_input: bool,
    a_to_b: bool,
    *,
    try_only: bool = False,
) -> tuple[int | None, bool]:
    if a_to_b == amount_specified_is_input:
        if try_only:
            return _try_get_amount_delta_a_exceeds(
                sqrt_price_current, sqrt_price_target, liquidity, amount_specified_is_input
            )
        return get_amount_delta_a(
            sqrt_price_current, sqrt_price_target, liquidity, amount_specified_is_input
        ), False
    if try_only:
        return _try_get_amount_delta_b_exceeds(
            sqrt_price_current, sqrt_price_target, liquidity, amount_specified_is_input
        )
    return get_amount_delta_b(
        sqrt_price_current, sqrt_price_target, liquidity, amount_specified_is_input
    ), False


def _get_amount_unfixed_delta(
    sqrt_price_current: int,
    sqrt_price_target: int,
    liquidity: int,
    amount_specified_is_input: bool,
    a_to_b: bool,
) -> int:
    if a_to_b == amount_specified_is_input:
        return get_amount_delta_b(
            sqrt_price_current, sqrt_price_target, liquidity, not amount_specified_is_input
        )
    return get_amount_delta_a(
        sqrt_price_current, sqrt_price_target, liquidity, not amount_specified_is_input
    )


def compute_swap_step(
    amount_remaining: int,
    fee_rate: int,
    liquidity: int,
    sqrt_price_current: int,
    sqrt_price_target: int,
    amount_specified_is_input: bool,
    a_to_b: bool,
) -> SwapStep:
    """Single-segment swap step (between two adjacent initialized ticks).

    Mirrors ``compute_swap`` in
    ``programs/whirlpool/src/math/swap_math.rs``. The returned ``next_sqrt_price``
    is either ``sqrt_price_target`` (max-swap segment, the fee charged exactly
    fills the price gap) or an interior price computed from the available
    ``amount_remaining``.
    """
    initial_amount_fixed_delta, initial_exceeds = _get_amount_fixed_delta(
        sqrt_price_current,
        sqrt_price_target,
        liquidity,
        amount_specified_is_input,
        a_to_b,
        try_only=True,
    )

    if amount_specified_is_input:
        amount_calc = (amount_remaining * (FEE_RATE_DENOMINATOR - fee_rate)) // FEE_RATE_DENOMINATOR
    else:
        amount_calc = amount_remaining

    if (
        not initial_exceeds
        and initial_amount_fixed_delta is not None
        and initial_amount_fixed_delta <= amount_calc
    ):
        next_sqrt_price = sqrt_price_target
    else:
        next_sqrt_price = get_next_sqrt_price(
            sqrt_price_current,
            liquidity,
            amount_calc,
            amount_specified_is_input,
            a_to_b,
        )

    is_max_swap = next_sqrt_price == sqrt_price_target

    amount_unfixed_delta = _get_amount_unfixed_delta(
        sqrt_price_current,
        next_sqrt_price,
        liquidity,
        amount_specified_is_input,
        a_to_b,
    )

    if not is_max_swap or initial_exceeds:
        amount_fixed_delta_value, _ = _get_amount_fixed_delta(
            sqrt_price_current,
            next_sqrt_price,
            liquidity,
            amount_specified_is_input,
            a_to_b,
        )
        if amount_fixed_delta_value is None:
            raise ValueError("compute_swap_step: fixed-delta calculation failed")
        amount_fixed_delta = amount_fixed_delta_value
    else:
        amount_fixed_delta = initial_amount_fixed_delta or 0

    if amount_specified_is_input:
        amount_in = amount_fixed_delta
        amount_out = amount_unfixed_delta
    else:
        amount_in = amount_unfixed_delta
        amount_out = amount_fixed_delta
        if amount_out > amount_remaining:
            amount_out = amount_remaining

    if amount_specified_is_input and not is_max_swap:
        fee_amount = amount_remaining - amount_in
    else:
        fee_amount = div_round_up(
            amount_in * fee_rate, FEE_RATE_DENOMINATOR - fee_rate
        )

    if fee_amount > U64_MAX:
        raise ValueError("compute_swap_step: fee u64 overflow")

    return SwapStep(
        amount_in=amount_in,
        amount_out=amount_out,
        next_sqrt_price=next_sqrt_price,
        fee_amount=fee_amount,
    )
