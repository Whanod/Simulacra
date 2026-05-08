"""``BundleTipPaid`` event + ``tip_outcomes`` ledger (PRD US-011 line 839,
US-005 line 410).

Asserts two pieces of the contract:

1. When a bundle lands and pays a non-zero tip, the engine appends a
   typed ``BundleTipPaidEvent`` payload to the durable ``_tip_outcomes``
   ledger AND emits the same payload on the ``BUNDLE_TIP_PAID`` bus
   channel.
2. The ledger is included in ``_snapshot_bundle_mutable_state`` so an
   ``atomic_state_boundary`` rollback drops entries written inside the
   boundary while preserving entries from earlier slots.
"""

from __future__ import annotations

import copy

from defi_sim.core.types import BundleTipPaidEvent
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import Bundle, MIN_BUNDLE_TIP_LAMPORTS, TipPayment
from defi_sim.engine.events import EventType
from defi_sim.engine.transactions import VersionedTransaction


SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "snapshot_interval": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "fifo"},
        "gas_model": {"type": "compute_unit"},
        "params": {"cost_token": "USDC"},
    },
}


def _build_bundle(tip_lamports: int = MIN_BUNDLE_TIP_LAMPORTS) -> Bundle:
    """Construct a minimal single-tx bundle with a standalone tip payment."""
    tx = VersionedTransaction(actions=[])
    return Bundle(
        txs=[tx],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip_lamports,
                recipient="tip-recipient-A",
            )
        ],
    )


def test_bundle_tip_paid_event_appended_and_emitted_on_landed_bundle() -> None:
    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)

    captured: list = []
    engine._bus.on(
        EventType.BUNDLE_TIP_PAID,
        lambda event: captured.append(event.data["bundle_tip_paid"]),
    )

    bundle = _build_bundle(tip_lamports=2_500)
    engine._execution_model.submit_bundle(bundle)
    engine.step()

    assert len(captured) == 1
    payload = captured[0]
    assert isinstance(payload, BundleTipPaidEvent)
    assert payload.tip_lamports == 2_500
    assert payload.tip_recipients == ("tip-recipient-A",)
    assert engine._tip_outcomes == [payload]


def test_atomic_boundary_reverts_tip_outcomes_appended_inside() -> None:
    """Append a tip-outcome inside an ``atomic_state_boundary`` and roll
    back: the ledger must drop the entry the boundary added while
    preserving the entry that landed in the previous slot.
    """
    spec = copy.deepcopy(SOLANA_SPEC)
    engine = build_engine(spec)

    bundle = _build_bundle(tip_lamports=4_000)
    engine._execution_model.submit_bundle(bundle)
    engine.step()
    pre_boundary = list(engine._tip_outcomes)
    assert len(pre_boundary) == 1

    with engine.atomic_state_boundary() as boundary:
        engine._tip_outcomes.append(
            BundleTipPaidEvent(
                slot=99,
                bundle_index=0,
                leader_pubkey=None,
                tip_lamports=12_345,
                tip_payments=(
                    TipPayment(
                        tx_index=0,
                        location="standalone_tx",
                        lamports=12_345,
                        recipient="rolled-back-recipient",
                    ),
                ),
                jito_stake_pool_share=0.0,
            )
        )
        boundary.rollback()

    assert engine._tip_outcomes == pre_boundary
