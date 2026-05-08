"""Test-isolation hooks for the snapshotter unit tests."""

from __future__ import annotations

import pytest

from defi_sim_solana.replay.slot_client import clear_slot_cache


@pytest.fixture(autouse=True)
def _clear_slot_lru_cache():
    """Drop the slot-client ``lru_cache`` between tests.

    The cache key is ``(provider_id, corpus_root, slot)``; fake clients in
    these tests share the same provider_id, so a stale entry from one test
    leaks into the next without this hook.
    """
    clear_slot_cache()
    yield
    clear_slot_cache()
