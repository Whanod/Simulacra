"""Integration test for ``tools.snapshotter.websocket`` (FIX-019).

Gated on ``SOLANA_RPC_URL``. Subscribes to ``slotSubscribe`` against the
configured Helius endpoint, drains a few slot notifications, then exits.
Proves the websocket adapter wires up against live Helius.
"""

from __future__ import annotations

import os

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SOLANA_RPC_URL"),
        reason="requires SOLANA_RPC_URL pointing at a paid Helius endpoint",
    ),
]


def test_derive_websocket_endpoint_swaps_https_for_wss() -> None:
    from tools.snapshotter.websocket import derive_websocket_endpoint

    assert derive_websocket_endpoint("https://example.com/x").startswith("wss://")
    assert derive_websocket_endpoint("http://example.com/x").startswith("ws://")
    assert derive_websocket_endpoint("wss://already").startswith("wss://")


def test_iter_subscribed_slots_yields_at_least_three_slots_under_limit() -> None:
    """Smoke test: confirm slotSubscribe streams real slot numbers."""
    from tools.snapshotter.websocket import iter_subscribed_slots

    slots = list(iter_subscribed_slots(max_slots=3))
    assert len(slots) == 3
    # Slot numbers strictly increase when the websocket is healthy.
    assert all(b > a for a, b in zip(slots, slots[1:])), (
        f"slotSubscribe returned non-monotonic slots: {slots}"
    )
