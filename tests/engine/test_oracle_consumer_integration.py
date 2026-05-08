"""Integration: a synthetic lending consumer pays the pull-oracle update cost.

PRD US-006 line 514:
    ``test_lending_action_with_pull_oracle_pays_update_cost`` —
    synthetic lending with a pull oracle; consumer's tx cost includes
    the oracle pull.

Rationale for "synthetic":
    No ``LendingMarket`` exists yet (Phase 3.2 / US-019 owns that). The
    integration we *can* lock in today is the consumer-side cost surface:
    when a borrower bundles an :class:`OracleUpdateAction` with their
    :class:`BorrowAction`, the borrower (not the oracle operator, not
    the validator) pays both halves of the pull-oracle cost — the
    per-CU priority fee on the update instruction *and* the flat
    ``update_lamport_cost`` aggregated by the oracle metric line.

    The unit test ``test_pull_oracle_cost_charged_to_consumer`` in
    ``test_oracles.py`` proves this for a single-action shape; this
    integration test adds the realistic-bundle dimension: the lending
    instruction sits in the *same* tx as the oracle update, both are
    stamped with the borrower's ``agent_id``, and the borrower's
    aggregate lamport outflow is the sum of both fee components.
"""

from __future__ import annotations

from defi_sim.core.types import BorrowAction
from defi_sim.engine.gas import ComputeUnitCost
from defi_sim.engine.oracles import (
    OracleSlotCost,
    PullOracle,
    oracle_costs_per_slot,
)


def test_lending_action_with_pull_oracle_pays_update_cost():
    """Borrower's tx fee = base+priority on the borrow + base+priority on the
    oracle pull + flat ``update_lamport_cost`` from the metric aggregator.

    Math is kept trivial so each component is independently auditable:

    * ``compute_unit_price_micro_lamports = 1_000_000`` → 1 lamport / CU.
    * ``BorrowAction``: 150_000 CU (DEFAULT_CU_LIMITS) → 150_000 lamport
      priority fee, ``cu_limit_source == "synthetic_default"``.
    * ``OracleUpdateAction``: 15_000 CU (stamped by ``pull()``) →
      15_000 lamport priority fee, ``cu_limit_source == "explicit"``.
    * Base fee: 5_000 lamport per single-signature tx, charged once per
      action's ``breakdown`` call here (the engine collapses sigs at the
      tx level; the per-action breakdown is the contract this test pins).
    * Flat lamport cost: 2_500 lamport on the pull slot (consumer-paid;
      ``operator_lamports == 0`` because no push oracle is in play).
    """
    oracle = PullOracle(
        oracle_id="SOL/USD",
        update_cu_cost=15_000,
        update_lamport_cost=2_500,
        staleness_tolerance_slots=10,
        price_source=lambda _slot: 100,
    )

    borrower_id = "borrower-1"
    pull_slot = 42

    # Pull-mode pattern: consumer checks staleness before consuming the
    # oracle. A never-pulled oracle is stale, so the borrower must
    # include the update instruction in their tx.
    assert oracle.is_stale(pull_slot) is True
    update_action = oracle.pull(pull_slot, agent_id=borrower_id)
    update_action.compute_unit_price_micro_lamports = 1_000_000  # 1 lamport / CU

    # Synthetic lending instruction: a BorrowAction declares the oracle
    # account it consults via ``oracle_account_ids`` so the parallel
    # scheduler can model contention (PRD US-006 line 491). Same agent
    # owns the bundle — both fee components fall on the borrower.
    borrow_action = BorrowAction(
        agent_id=borrower_id,
        token="USDC",
        amount=1_000,
        oracle_account_ids=frozenset({"SOL/USD"}),
        compute_unit_price_micro_lamports=1_000_000,
    )
    assert borrow_action.oracle_account_ids == frozenset({"SOL/USD"})
    assert update_action.agent_id == borrower_id

    cost_model = ComputeUnitCost()

    # CU half — the borrow instruction.
    borrow_breakdown = cost_model.breakdown(borrow_action, round=pull_slot)
    assert borrow_breakdown.cu_limit_source == "synthetic_default"
    assert borrow_breakdown.priority_fee_lamports == 150_000
    assert borrow_breakdown.base_fee_lamports == 5_000

    # CU half — the oracle update instruction.
    oracle_breakdown = cost_model.breakdown(update_action, round=pull_slot)
    assert oracle_breakdown.cu_limit_source == "explicit"
    assert oracle_breakdown.priority_fee_lamports == 15_000
    assert oracle_breakdown.base_fee_lamports == 5_000

    # Lamport half — the consumer-paid flat lamport cost surfaces only
    # via the oracle metric aggregator. No push oracle in play, so the
    # operator_lamports column is zero.
    metric = oracle_costs_per_slot(
        pull_oracle_pulls={"SOL/USD": [pull_slot]},
        pull_oracles={"SOL/USD": oracle},
    )
    assert metric == [
        OracleSlotCost(
            slot=pull_slot,
            cu=15_000,
            lamports=2_500,
            operator_lamports=0,
        )
    ]

    # End-to-end assertion: the borrower's total lamport outflow on this
    # bundled tx is base + priority on each instruction PLUS the flat
    # oracle lamport cost. Anchoring the sum guards against any future
    # refactor that double-counts (e.g. if ``update_lamport_cost`` were
    # accidentally folded into the CU priority fee, this number would
    # change and the test would fail loudly).
    expected_total_borrower_lamports = (
        borrow_breakdown.total_lamports
        + oracle_breakdown.total_lamports
        + metric[0].lamports
    )
    assert expected_total_borrower_lamports == (
        5_000 + 150_000  # borrow base + priority
        + 5_000 + 15_000  # oracle update base + priority
        + 2_500  # flat consumer-paid oracle lamport cost
    )
    assert expected_total_borrower_lamports == 177_500
