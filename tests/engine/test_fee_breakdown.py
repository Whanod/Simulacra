"""Tests for the FeeBreakdown dataclass shape (task 0.4.4).

The dataclass is a pure container — `breakdown()` lands in task 0.4.5 and
will exercise the math. These tests lock the field set and immutability so
the validator-economics path (Phase 1.10) can rely on the shape.
"""

from __future__ import annotations

import dataclasses

import pytest

from defi_sim.engine.gas import FeeBreakdown


def test_fee_breakdown_has_expected_fields() -> None:
    fb = FeeBreakdown(
        base_fee_lamports=5_000,
        base_fee_burned_lamports=2_500,
        base_fee_to_validator_lamports=2_500,
        priority_fee_lamports=0,
        total_lamports=5_000,
    )
    assert fb.base_fee_lamports == 5_000
    assert fb.base_fee_burned_lamports == 2_500
    assert fb.base_fee_to_validator_lamports == 2_500
    assert fb.priority_fee_lamports == 0
    assert fb.total_lamports == 5_000


def test_fee_breakdown_is_frozen() -> None:
    fb = FeeBreakdown(
        base_fee_lamports=5_000,
        base_fee_burned_lamports=2_500,
        base_fee_to_validator_lamports=2_500,
        priority_fee_lamports=0,
        total_lamports=5_000,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        fb.total_lamports = 999  # type: ignore[misc]


def test_fee_breakdown_field_set_is_locked() -> None:
    expected = {
        "base_fee_lamports",
        "base_fee_burned_lamports",
        "base_fee_to_validator_lamports",
        "priority_fee_lamports",
        "total_lamports",
        "cu_limit_source",
    }
    actual = {f.name for f in dataclasses.fields(FeeBreakdown)}
    assert actual == expected
