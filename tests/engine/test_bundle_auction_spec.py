"""BundleAuctionSpec mirrors BundleAuction (US-011, PRD line 890)."""

from __future__ import annotations

import pytest

from defi_sim.engine.bundle import MAX_BUNDLE_TXS, MIN_BUNDLE_TIP_LAMPORTS
from defi_sim.engine.bundle_auction import (
    DEFAULT_JITO_TIP_ACCOUNTS,
    BundleAuction,
)
from defi_sim.engine.specs import BundleAuctionSpec


def test_bundle_auction_spec_defaults_match_prd() -> None:
    spec = BundleAuctionSpec()
    assert spec.max_bundles_per_slot == 5
    assert spec.jito_stake_pool_share == 0.05
    assert spec.tip_account_set == DEFAULT_JITO_TIP_ACCOUNTS
    assert len(spec.tip_account_set) == 8
    assert spec.max_bundle_txs == MAX_BUNDLE_TXS
    assert spec.min_bundle_tip_lamports == MIN_BUNDLE_TIP_LAMPORTS


def test_bundle_auction_spec_to_auction_uses_spec_values() -> None:
    custom_tips = ("tip-A", "tip-B")
    spec = BundleAuctionSpec(
        max_bundles_per_slot=2,
        jito_stake_pool_share=0.1,
        tip_account_set=custom_tips,
        max_bundle_txs=3,
        min_bundle_tip_lamports=2_000,
    )
    auction = spec.to_bundle_auction()
    assert isinstance(auction, BundleAuction)
    assert auction.max_bundles_per_slot == 2
    assert auction.jito_stake_pool_share == 0.1
    assert auction.tip_account_set == custom_tips
    assert auction.max_bundle_txs == 3
    assert auction.min_bundle_tip_lamports == 2_000


def test_bundle_auction_spec_from_dict_uses_defaults_when_partial() -> None:
    spec = BundleAuctionSpec.from_dict({})
    assert spec == BundleAuctionSpec()


def test_bundle_auction_spec_from_dict_round_trips_overrides() -> None:
    spec = BundleAuctionSpec.from_dict(
        {
            "max_bundles_per_slot": 10,
            "jito_stake_pool_share": 0.07,
            "tip_account_set": ["one", "two", "three"],
            "max_bundle_txs": 4,
            "min_bundle_tip_lamports": 5_000,
        }
    )
    assert spec == BundleAuctionSpec(
        max_bundles_per_slot=10,
        jito_stake_pool_share=0.07,
        tip_account_set=("one", "two", "three"),
        max_bundle_txs=4,
        min_bundle_tip_lamports=5_000,
    )


def test_bundle_auction_spec_from_dict_coerces_tip_accounts_to_strings() -> None:
    spec = BundleAuctionSpec.from_dict({"tip_account_set": [1, 2, 3]})
    assert spec.tip_account_set == ("1", "2", "3")


def test_bundle_auction_spec_to_auction_validates_via_constructor() -> None:
    """Spec values that violate auction invariants surface at to_auction() time."""
    bad = BundleAuctionSpec(jito_stake_pool_share=1.5)
    with pytest.raises(ValueError, match="jito_stake_pool_share"):
        bad.to_bundle_auction()


def test_bundle_auction_rejects_empty_tip_account_set() -> None:
    """Direct ctor guard: empty tip set is invalid (validators must have a target)."""
    with pytest.raises(ValueError, match="tip_account_set"):
        BundleAuction(tip_account_set=())
