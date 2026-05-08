"""Phase 1 DoD assertion (PRD line 21).

A single end-to-end test that drives the Solana execution stack with a
three-transaction bundle and confirms each Phase 1 mechanism:

* CU budget enforcement (US-002 / US-008)
* Account-lock parallel scheduling (US-003)
* Jito bundle auction selecting the bundle and capturing the tip (US-011)
* Validator tip capture (US-012)
* Blockhash-expiry admission drop (US-014 line 1108)
* Fork reorg reverting tip outcomes (US-014 line 1124)

The test is intentionally one assertion bundle per mechanism so a
regression in any single mechanism trips a named assertion rather than
an opaque mismatch elsewhere.
"""

from __future__ import annotations

from defi_sim.core.types import BundleTipPaidEvent, SwapAction
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import Bundle, MIN_BUNDLE_TIP_LAMPORTS, TipPayment
from defi_sim.engine.events import EventType
from defi_sim.engine.transactions import VersionedTransaction


def _solana_spec(
    *,
    fork_probability: float = 0.0,
    num_rounds: int = 1,
) -> dict:
    return {
        "market": {
            "type": "cfamm",
            "tokens": [
                {
                    "id": "SOL",
                    "symbol": "SOL",
                    "decimals": 9,
                    "native": True,
                    "standard": "native",
                },
                {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
            ],
            "params": {
                "initial_liquidity": 10_000_000,
                "collateral_token": "USDC",
            },
        },
        "agents": [
            {
                "type": "noise",
                "agent_id": "noise-1",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {
                    "USDC": 1_000_000_000,
                    "SOL": 1_000_000_000,
                },
            },
        ],
        "num_rounds": num_rounds,
        "snapshot_interval": 1,
        "seed": 42,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
            "params": {
                "cost_token": "USDC",
                "blockhash_history": True,
                "fork_spec": {
                    "fork_probability_per_slot": fork_probability,
                    "max_reorg_depth_slots": 3,
                    "seed": 13,
                },
            },
        },
    }


def _three_tx_bundle(*, tip_lamports: int, cu_per_tx: int) -> Bundle:
    """Build a 3-tx bundle of SOL→USDC swaps with a standalone tip."""
    txs = [
        VersionedTransaction(
            actions=[
                SwapAction(
                    agent_id="noise-1",
                    token_in="SOL",
                    token_out="USDC",
                    amount_in=1,
                    compute_unit_limit=cu_per_tx,
                )
            ],
        )
        for _ in range(3)
    ]
    return Bundle(
        txs=txs,
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip_lamports,
                recipient="tip-recipient",
            )
        ],
    )


def test_three_tx_bundle_produces_realistic_economics() -> None:
    """PRD Phase 1 DoD (PRD line 10 / line 21).

    Drives the Solana stack end-to-end with a three-transaction bundle
    and asserts every Phase 1 mechanism fires.
    """
    # ── 1. Land the bundle: CU enforcement, locks, auction, tip ──────
    cu_per_tx = 100_000
    tip_lamports = max(MIN_BUNDLE_TIP_LAMPORTS, 5_000)
    engine = build_engine(_solana_spec())

    captured_tip_events: list[BundleTipPaidEvent] = []
    engine._bus.on(
        EventType.BUNDLE_TIP_PAID,
        lambda event: captured_tip_events.append(event.data["bundle_tip_paid"]),
    )

    bundle = _three_tx_bundle(tip_lamports=tip_lamports, cu_per_tx=cu_per_tx)
    engine._execution_model.submit_bundle(bundle)
    snapshot = engine.step()

    # Bundle was admitted, scheduled, and landed atomically.
    assert len(snapshot.bundle_outcomes) == 1, (
        f"expected exactly one bundle outcome, got {snapshot.bundle_outcomes!r}"
    )
    outcome = snapshot.bundle_outcomes[0]
    assert outcome.status == "landed", (
        f"bundle did not land: status={outcome.status!r} "
        f"drop_reason={outcome.drop_reason!r}"
    )
    assert outcome.num_txs == 3
    # Account-lock scheduling: a 3-tx bundle that all touch the cfamm pool
    # serializes inside the bundle without conflict drops (PRD US-003).
    assert outcome.drop_reason is None
    # CU budget enforcement: the bundle's reported total_cu equals the sum
    # of the per-tx caps (PRD US-002 / US-008).
    assert outcome.total_cu == cu_per_tx * 3, (
        f"total_cu {outcome.total_cu} != {cu_per_tx * 3} (per-tx CU budget "
        f"not flowing through bundle accounting)"
    )

    # Jito auction captured the tip and split it via the validator's
    # stake-pool revenue rule (PRD US-011 / US-012).
    assert outcome.tip_lamports == tip_lamports
    assert outcome.validator_revenue_lamports + outcome.stake_pool_revenue_lamports == tip_lamports
    assert outcome.validator_revenue_lamports > 0, (
        "validator captured zero tip — auction → validator revenue path broken"
    )

    # BUNDLE_TIP_PAID event fired and tip ledger has the same payload.
    assert len(captured_tip_events) == 1
    assert captured_tip_events[0].tip_lamports == tip_lamports
    assert engine._tip_outcomes == captured_tip_events

    # ── 2. Blockhash expiry: a stale-blockhash action is admit-dropped ──
    bh_engine = build_engine(_solana_spec(num_rounds=1))
    history = bh_engine._execution_model._blockhash_history
    assert history is not None, "blockhash_history not wired through spec"
    history.record(slot=0, blockhash="bh-genesis")
    expired_action = SwapAction(
        agent_id="noise-1",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        compute_unit_limit=cu_per_tx,
        recent_blockhash="bh-genesis",
    )
    # SolanaLikeExecution's default BlockhashHistory validity is 150
    # slots; admit at slot 200 places the genesis blockhash comfortably
    # past the window so the drop reason is unambiguous.
    _, dropped_pairs = bh_engine._execution_model.admit(
        actions=[expired_action], round=200
    )
    assert any(reason == "blockhash_expired" for _, reason in dropped_pairs), (
        f"stale blockhash not dropped at admit: {dropped_pairs!r}"
    )

    # ── 3. Fork reorg reverts a landed bundle's tip outcomes ─────────
    fork_engine = build_engine(_solana_spec(fork_probability=1.0))
    fork_bundle = _three_tx_bundle(
        tip_lamports=tip_lamports, cu_per_tx=cu_per_tx
    )
    fork_engine._execution_model.submit_bundle(fork_bundle)
    fork_engine.step()
    # With fork_probability_per_slot=1.0 every slot triggers a reorg, so
    # the bundle that landed in this slot must have its tip ledger
    # reverted by the post-fork rollback (PRD US-014 line 1124).
    assert fork_engine._tip_outcomes == [], (
        f"fork reorg did not revert tip outcomes: {fork_engine._tip_outcomes!r}"
    )
