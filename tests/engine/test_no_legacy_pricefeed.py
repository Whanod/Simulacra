"""Locks the US-006 step 1.8b sweep (PRD line 458).

Once 1.8b lands, the legacy ``PriceFeed`` ABC and the
``LegacyFeedAsOracle`` shim are gone for good. These tests fail loudly
if either is reintroduced — a typo or a half-finished revert can't
silently bring back the chain-neutral price-source interface.
"""

from __future__ import annotations

import pytest


def test_pricefeed_class_does_not_exist():
    """``from defi_sim.engine.feeds import PriceFeed`` must raise."""
    with pytest.raises(ImportError):
        from defi_sim.engine.feeds import PriceFeed  # noqa: F401


def test_legacy_feed_as_oracle_class_does_not_exist():
    """``from defi_sim.engine.oracles import LegacyFeedAsOracle`` must raise."""
    with pytest.raises(ImportError):
        from defi_sim.engine.oracles import LegacyFeedAsOracle  # noqa: F401
