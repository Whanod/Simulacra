"""Fixed-point arithmetic helpers.

Every token carries its own decimals/scale, so cross-decimal operations
are common and error-prone without helpers. All functions are pure.
"""

from __future__ import annotations


def mul_fp(a: int, b: int, scale: int) -> int:
    """Multiply two fixed-point numbers: (a * b) / scale.
    Uses integer arithmetic with rounding toward zero."""
    return (a * b) // scale


def div_fp(a: int, b: int, scale: int) -> int:
    """Divide two fixed-point numbers: (a * scale) / b.
    Raises ZeroDivisionError if b is zero."""
    return (a * scale) // b


def convert_scale(value: int, from_decimals: int, to_decimals: int) -> int:
    """Convert a fixed-point value between decimal scales.
    Example: convert_scale(1_000_000, 6, 18) -> 1_000_000_000_000_000_000"""
    if to_decimals >= from_decimals:
        return value * (10 ** (to_decimals - from_decimals))
    return value // (10 ** (from_decimals - to_decimals))


def mul_fp_round_up(a: int, b: int, scale: int) -> int:
    """Multiply with ceiling rounding. Used for debt/fee calculations
    where rounding must favor the protocol."""
    return (a * b + scale - 1) // scale


def div_fp_round_up(a: int, b: int, scale: int) -> int:
    """Divide with ceiling rounding."""
    return (a * scale + b - 1) // b


def sqrt_fp(value: int, scale: int) -> int:
    """Integer square root scaled to fixed-point.
    sqrt_fp(4 * scale, scale) == 2 * scale."""
    if value == 0:
        return 0
    # Newton's method on value * scale to maintain precision
    x = value * scale
    y = x
    while True:
        y_next = (y + x // y) // 2
        if y_next >= y:
            break
        y = y_next
    return y


def weighted_average_fp(values: list[int], weights: list[int], scale: int) -> int:
    """Weighted average in fixed-point. weights must sum to scale."""
    return sum(mul_fp(v, w, scale) for v, w in zip(values, weights))


def isqrt(n: int) -> int:
    """Integer square root via Newton's method."""
    if n < 0:
        raise ValueError("isqrt requires non-negative input")
    if n == 0:
        return 0
    x = n
    y = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x
