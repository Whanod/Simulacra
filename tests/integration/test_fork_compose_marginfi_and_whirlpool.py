"""Two-protocol composition fork test for MarginFi + Whirlpool (PRD line 709).

**Gated test.** This file pins PRD US-003's gated bullet at line 709: once 3.2
MarginFi lands (with a `MarginFiStateHydrator` and a `ForkableMarket` impl),
the composition path must work for a second non-AMM parser shape (lending
reserves + lending positions, not just pool fragments). The test mirrors
``test_fork_compose_whirlpool_and_dlmm`` but pairs Whirlpool with MarginFi so
the framework's "more than one parser shape on day one" guarantee extends to
the lending taxonomy (``lending_reserve`` / ``lending_position`` fragments).

The body is skipped until the MarginFi hydrator exists. Lighting it up is a
two-step change at that point: drop the skip marker and import the real
hydrator + market class. The fixture wiring, manifest reader, and assertion
shape are kept pre-built so the gated lift is mechanical, not architectural.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "Gated on 3.2 MarginFi: requires MarginFiStateHydrator and a "
        "ForkableMarket subclass plus a corpus slot whose manifest declares "
        "MarginFi reserves alongside Whirlpool pools. Drop this marker when "
        "3.2 lands and replace the placeholder hydrator import below."
    )
)


def test_fork_compose_marginfi_and_whirlpool() -> None:
    """PRD line 709 — fork MarginFi + Whirlpool at one slot, assert each
    protocol's expected accounts flow into the engine and ``run()`` ticks
    rounds without errors.

    When 3.2 lands, this should mirror
    ``tests/integration/test_fork_compose_whirlpool_and_dlmm.py``:

    1. Assert ``initial.by_protocol("MarginFi")`` covers the manifest's
       expected reserve pubkeys, and ``by_protocol("Whirlpool")`` covers the
       Whirlpool pool pubkeys, with disjoint pubkey sets.
    2. Assert each fragment routed through the right hydrator by sampling
       payload keys (Whirlpool fragments expose ``tick_current_index``;
       MarginFi reserve fragments expose ``deposit_limit`` / ``borrow_limit``
       or whichever fields the 3.2 hydrator decides on — fix this assertion
       when the hydrator's payload contract is finalized).
    3. Build the engine with ``build_forked_engine``, assert both markets
       land in ``engine._market.markets``, and run two rounds of a
       ``_SilentAgent`` without raising.

    Pinning all three claims in one test (instead of three) keeps the
    regression signal clean: "the MarginFi+Whirlpool composition is broken"
    rather than three correlated reds on the same fixture.
    """
    raise AssertionError(
        "test_fork_compose_marginfi_and_whirlpool body is intentionally "
        "absent until 3.2 MarginFi ships its StateHydrator and ForkableMarket."
    )
