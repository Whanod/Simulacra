"""``Bundle`` dataclass invariant tests (PRD US-011 line 797)."""

from __future__ import annotations

import pytest

from defi_sim.core.types import DEFAULT_CU_LIMIT_FALLBACK, SwapAction
from defi_sim.engine.bundle import (
    MAX_BUNDLE_TXS,
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
    TipPayment,
)
from defi_sim.engine.transactions import VersionedTransaction


def _vtx() -> VersionedTransaction:
    return VersionedTransaction(
        actions=[SwapAction(agent_id="a", token_in="SOL", token_out="USDC", amount_in=1)]
    )


def test_bundle_within_limits_accepts() -> None:
    bundle = Bundle(
        txs=[_vtx(), _vtx()],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="tip-acct-1",
            )
        ],
    )
    assert bundle.tip_lamports == MIN_BUNDLE_TIP_LAMPORTS
    # Inner actions without an explicit ``compute_unit_limit`` charge the
    # admit-path default so jito-searcher bundles can't bypass slot-CU.
    assert bundle.total_cu == 2 * DEFAULT_CU_LIMIT_FALLBACK


def test_bundle_too_large_raises() -> None:
    with pytest.raises(ValueError, match="exceeds Jito max"):
        Bundle(
            txs=[_vtx() for _ in range(MAX_BUNDLE_TXS + 1)],
            tip_payments=[
                TipPayment(
                    tx_index=0,
                    location="standalone_tx",
                    lamports=MIN_BUNDLE_TIP_LAMPORTS,
                    recipient="tip-acct-1",
                )
            ],
        )


def test_bundle_below_min_tip_raises() -> None:
    with pytest.raises(ValueError, match="below Jito minimum"):
        Bundle(
            txs=[_vtx()],
            tip_payments=[
                TipPayment(
                    tx_index=0,
                    location="standalone_tx",
                    lamports=MIN_BUNDLE_TIP_LAMPORTS - 1,
                    recipient="tip-acct-1",
                )
            ],
        )


def test_bundle_no_tip_payments_raises() -> None:
    with pytest.raises(ValueError, match="below Jito minimum"):
        Bundle(txs=[_vtx()], tip_payments=[])


def test_tip_payment_index_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match="tx_index out of range"):
        Bundle(
            txs=[_vtx()],
            tip_payments=[
                TipPayment(
                    tx_index=1,
                    location="standalone_tx",
                    lamports=MIN_BUNDLE_TIP_LAMPORTS,
                    recipient="tip-acct-1",
                )
            ],
        )


def test_tip_lamports_sum_across_payments() -> None:
    bundle = Bundle(
        txs=[_vtx(), _vtx()],
        tip_payments=[
            TipPayment(tx_index=0, location="instruction", lamports=600, recipient="t1"),
            TipPayment(tx_index=1, location="standalone_tx", lamports=400, recipient="t2"),
        ],
    )
    assert bundle.tip_lamports == 1_000


def test_tip_recipient_single_recipient_returns_address() -> None:
    """PRD line 814: derived helper for the common single-recipient case."""
    bundle = Bundle(
        txs=[_vtx(), _vtx()],
        tip_payments=[
            TipPayment(tx_index=0, location="instruction", lamports=600, recipient="t1"),
            TipPayment(tx_index=1, location="standalone_tx", lamports=400, recipient="t1"),
        ],
    )
    assert bundle.tip_recipient == "t1"


def test_tip_recipient_split_recipients_returns_none() -> None:
    bundle = Bundle(
        txs=[_vtx(), _vtx()],
        tip_payments=[
            TipPayment(tx_index=0, location="instruction", lamports=600, recipient="t1"),
            TipPayment(tx_index=1, location="standalone_tx", lamports=400, recipient="t2"),
        ],
    )
    assert bundle.tip_recipient is None
