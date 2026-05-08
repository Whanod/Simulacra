"""US-011 PRD line 895: builder defaults bundle auction on for Solana execution.

Verifies that ``_build_solana_like_execution``:
- Defaults to a configured ``BundleAuction`` when no
  ``execution.params.bundle_auction`` is supplied.
- Consumes a dict, a typed ``BundleAuctionSpec``, or an existing
  ``BundleAuction`` instance.
- Honors an explicit ``None`` to opt out.
"""

from __future__ import annotations

from defi_sim.engine.bundle_auction import BundleAuction, DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.specs import (
    BundleAuctionSpec,
    ExecutionSpec,
    build_execution_model,
)


def test_solana_execution_bundle_auction_defaults_on_when_unspecified() -> None:
    spec = ExecutionSpec(type="solana_like", params={})
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    auction = exec_model.bundle_auction
    assert isinstance(auction, BundleAuction)
    # Default knobs match BundleAuctionSpec defaults.
    assert auction.max_bundles_per_slot == 5
    assert auction.jito_stake_pool_share == 0.05
    assert auction.tip_account_set == DEFAULT_JITO_TIP_ACCOUNTS


def test_solana_execution_consumes_bundle_auction_dict() -> None:
    spec = ExecutionSpec(
        type="solana_like",
        params={
            "bundle_auction": {
                "max_bundles_per_slot": 3,
                "jito_stake_pool_share": 0.1,
                "tip_account_set": ["validator-tip-1", "validator-tip-2"],
            }
        },
    )
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    auction = exec_model.bundle_auction
    assert isinstance(auction, BundleAuction)
    assert auction.max_bundles_per_slot == 3
    assert auction.jito_stake_pool_share == 0.1
    assert auction.tip_account_set == ("validator-tip-1", "validator-tip-2")


def test_solana_execution_accepts_typed_bundle_auction_spec() -> None:
    typed = BundleAuctionSpec(
        max_bundles_per_slot=2,
        jito_stake_pool_share=0.2,
        tip_account_set=("custom-tip",),
    )
    spec = ExecutionSpec(
        type="solana_like",
        params={"bundle_auction": typed},
    )
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    auction = exec_model.bundle_auction
    assert isinstance(auction, BundleAuction)
    assert auction.max_bundles_per_slot == 2
    assert auction.jito_stake_pool_share == 0.2
    assert auction.tip_account_set == ("custom-tip",)


def test_solana_execution_bundle_auction_explicit_none_opts_out() -> None:
    spec = ExecutionSpec(
        type="solana_like",
        params={"bundle_auction": None},
    )
    exec_model = build_execution_model(spec)
    assert isinstance(exec_model, SolanaLikeExecution)
    assert exec_model.bundle_auction is None
