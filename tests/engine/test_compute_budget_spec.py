"""ComputeBudgetSpec mirrors ComputeBudget (US-002, PRD line 148)."""

from __future__ import annotations

import pytest

from defi_sim.engine.compute_budget import ComputeBudget, ComputeBudgetSource
from defi_sim.engine.specs import (
    ComputeBudgetSourceSpec,
    ComputeBudgetSpec,
    build_compute_budget,
)


def test_compute_budget_spec_defaults_match_current_mainnet() -> None:
    spec = ComputeBudgetSpec()
    assert spec.per_slot == 60_000_000
    assert spec.per_tx == 1_400_000
    assert spec.per_writable_account == 12_000_000
    assert spec.source is None


def test_compute_budget_spec_to_compute_budget_round_trips_defaults() -> None:
    assert ComputeBudgetSpec().to_compute_budget() == ComputeBudget()


def test_compute_budget_spec_from_dict_uses_defaults_when_partial() -> None:
    spec = ComputeBudgetSpec.from_dict({})
    assert spec == ComputeBudgetSpec()
    assert spec.to_compute_budget() == ComputeBudget()


def test_compute_budget_spec_round_trips_source_metadata() -> None:
    spec = ComputeBudgetSpec.from_dict(
        {
            "per_slot": 48_000_000,
            "per_tx": 1_400_000,
            "per_writable_account": 12_000_000,
            "source": {"activation_slot": 12345, "reference": "SIMD-0123"},
        }
    )
    assert spec.source == ComputeBudgetSourceSpec(
        activation_slot=12345, reference="SIMD-0123"
    )
    budget = spec.to_compute_budget()
    assert budget.per_slot == 48_000_000
    assert budget.source == ComputeBudgetSource(
        activation_slot=12345, reference="SIMD-0123"
    )


def test_build_compute_budget_defaults_to_current_mainnet_when_none() -> None:
    """PRD line 148: solana_like default is ComputeBudget() (current mainnet)."""
    assert build_compute_budget(None) == ComputeBudget()


def test_build_compute_budget_accepts_mapping() -> None:
    budget = build_compute_budget({"per_slot": 30_000_000})
    assert budget.per_slot == 30_000_000
    assert budget.per_tx == 1_400_000


def test_build_compute_budget_accepts_typed_spec() -> None:
    budget = build_compute_budget(ComputeBudgetSpec(per_tx=1_000_000))
    assert budget.per_tx == 1_000_000


def test_compute_budget_spec_from_compute_budget_round_trips() -> None:
    src = ComputeBudgetSource(activation_slot=99, reference="SIMD-0042")
    budget = ComputeBudget(per_slot=12, per_tx=4, per_writable_account=2, source=src)
    spec = ComputeBudgetSpec.from_compute_budget(budget)
    assert spec.to_compute_budget() == budget


def test_build_compute_budget_rejects_bad_type() -> None:
    with pytest.raises(TypeError):
        build_compute_budget(42)  # type: ignore[arg-type]
