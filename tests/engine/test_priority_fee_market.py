"""Unit tests for PriorityFeeMarket (PRD US-010, lines 756-762)."""

from __future__ import annotations

from defi_sim.engine.priority_fee_market import PriorityFeeMarket


def test_quote_returns_floor_for_unseen_account() -> None:
    """PRD line 756: quote for never-observed account returns the floor."""
    market = PriorityFeeMarket(floor_micro_lamports=42)
    assert market.quote("never_seen", 50) == 42


def test_observe_then_quote_returns_observed_value() -> None:
    """PRD line 757: single observation above floor, p50 returns observed price."""
    market = PriorityFeeMarket(floor_micro_lamports=1)
    market.observe("pool_A", slot=10, price_micro_lamports=5_000)
    assert market.quote("pool_A", 50) == 5_000


def test_observed_below_floor_quote_clamps_to_floor() -> None:
    """PRD line 758: observation of 0 with floor=1 returns 1, not 0."""
    market = PriorityFeeMarket(floor_micro_lamports=1)
    market.observe("pool_A", slot=10, price_micro_lamports=0)
    assert market.quote("pool_A", 50) == 1


def test_percentiles_match_distribution() -> None:
    """PRD line 759: feed 100 ascending values; p25/p50/p75/p99 match observed quartiles."""
    market = PriorityFeeMarket(floor_micro_lamports=1)
    for i in range(1, 101):
        market.observe("pool_A", slot=i, price_micro_lamports=i)
    pcts = market.percentiles("pool_A")
    # Expected from idx = (p * (n-1)) // 100 with n=100, prices=[1..100]:
    # p25 -> idx 24 -> 25; p50 -> 49 -> 50; p75 -> 74 -> 75; p90 -> 89 -> 90; p99 -> 98 -> 99.
    # Tolerance ±1 covers EWMA smoothing per the PRD line 759 spec.
    assert abs(pcts[25] - 25) <= 1
    assert abs(pcts[50] - 50) <= 1
    assert abs(pcts[75] - 75) <= 1
    assert abs(pcts[90] - 90) <= 1
    assert abs(pcts[99] - 99) <= 1


def test_ewma_smoothes_outlier() -> None:
    """PRD line 760: 99 stable values + 1 outlier; p50 moves by less than the outlier's magnitude."""
    market = PriorityFeeMarket(floor_micro_lamports=1)
    stable_price = 100
    for slot in range(99):
        market.observe("pool_A", slot=slot, price_micro_lamports=stable_price)
    p50_before = market.quote("pool_A", 50)

    outlier_price = 10_000
    market.observe("pool_A", slot=99, price_micro_lamports=outlier_price)
    p50_after = market.quote("pool_A", 50)

    movement = abs(p50_after - p50_before)
    outlier_magnitude = abs(outlier_price - p50_before)
    assert movement < outlier_magnitude


def test_read_locks_do_not_update_market() -> None:
    """PRD line 762: read-locking actions must not move the market.

    The admit-time hook (PRD line 738) iterates each ``LockedAction``'s
    ``write_locks`` and calls ``observe(account_id, slot, price)``.
    Read-locks are observational only. This test simulates that hook and
    asserts that an account that only ever appears in ``read_locks`` is
    never observed — its quote stays at the configured floor.
    """
    from defi_sim.engine.scheduler import LockedAction
    from defi_sim.core.types import Action

    market = PriorityFeeMarket(floor_micro_lamports=7)

    read_only = LockedAction(
        action=Action(agent_id="a1"),
        read_locks=frozenset({"pool_A"}),
        write_locks=frozenset(),
    )
    writer = LockedAction(
        action=Action(agent_id="a2"),
        read_locks=frozenset(),
        write_locks=frozenset({"pool_B"}),
    )

    slot = 5
    price = 9_999
    for locked in (read_only, writer):
        for account_id in locked.write_locks:
            market.observe(account_id, slot, price)

    assert market.quote("pool_A", 50) == 7
    assert market.percentiles("pool_A") == {p: 7 for p in (25, 50, 75, 90, 99)}
    assert market.quote("pool_B", 50) == price


def test_smoothed_baseline_uses_ewma_half_life() -> None:
    """PRD line 737: aggregation maintains an EWMA-smoothed baseline per
    account using ``ewma_half_life_slots`` so consumers (e.g. line 745's
    change-detection event emission) have a stable scalar reference.

    Initial observation seeds the baseline exactly (no warmup distortion).
    Subsequent observations move the baseline by ``alpha = 1 - 0.5^(1/H)``
    of the gap between the new value and the prior baseline.
    """
    market = PriorityFeeMarket(ewma_half_life_slots=30, floor_micro_lamports=1)
    assert market.smoothed_baseline("unseen") == 1  # floor for unseen

    market.observe("pool_A", slot=0, price_micro_lamports=1_000)
    assert market.smoothed_baseline("pool_A") == 1_000  # seeded by first obs

    # 100 stable observations at 1_000 — EWMA stays at 1_000.
    for slot in range(1, 100):
        market.observe("pool_A", slot=slot, price_micro_lamports=1_000)
    assert market.smoothed_baseline("pool_A") == 1_000

    # One outlier of 100_000 moves the baseline by ~alpha * (100_000 - 1_000).
    # alpha = 1 - 0.5^(1/30) ≈ 0.0228 → expected delta ≈ 2_257.
    market.observe("pool_A", slot=100, price_micro_lamports=100_000)
    baseline_after = market.smoothed_baseline("pool_A")
    assert 1_500 < baseline_after < 5_000, baseline_after


def test_ewma_smoothing_configurable() -> None:
    """PRD US-010 DoD line 771: EWMA smoothing must be configurable.

    Verify that the ``ewma_half_life_slots`` knob actually changes the
    smoothing behaviour. Same observation stream fed into two markets
    with different half-lives must produce materially different EWMA
    baselines: a short half-life tracks the latest observation closely,
    a long half-life barely moves from the seed.
    """
    fast = PriorityFeeMarket(ewma_half_life_slots=1, floor_micro_lamports=1)
    slow = PriorityFeeMarket(ewma_half_life_slots=10_000, floor_micro_lamports=1)

    seed = 100
    fast.observe("pool_A", slot=0, price_micro_lamports=seed)
    slow.observe("pool_A", slot=0, price_micro_lamports=seed)

    spike = 10_000
    fast.observe("pool_A", slot=1, price_micro_lamports=spike)
    slow.observe("pool_A", slot=1, price_micro_lamports=spike)

    fast_baseline = fast.smoothed_baseline("pool_A")
    slow_baseline = slow.smoothed_baseline("pool_A")

    # Short half-life: alpha = 1 - 0.5^1 = 0.5, so baseline ≈ (seed + spike) / 2 = 5_050.
    assert 4_000 < fast_baseline < 6_000, fast_baseline
    # Long half-life: alpha ≈ 6.93e-5, so baseline barely budges from seed.
    assert seed <= slow_baseline < seed + 5, slow_baseline
    # Configurability is meaningful only if the two diverge by an order of magnitude.
    assert fast_baseline >= slow_baseline * 10


def test_window_drops_old_observations() -> None:
    """PRD line 761: feed slots 0..200, only the last window_slots remain in the distribution."""
    market = PriorityFeeMarket(window_slots=150, floor_micro_lamports=1)
    # Slots 0..49: low price 1 (50 observations).
    for slot in range(50):
        market.observe("pool_A", slot=slot, price_micro_lamports=1)
    # Slots 50..200: high price 1_000 (151 observations).
    for slot in range(50, 201):
        market.observe("pool_A", slot=slot, price_micro_lamports=1_000)
    # Total fed: 201; window=150, so the oldest 51 (slots 0..50) drop out,
    # leaving slots 51..200 (150 observations, all price=1_000).
    # Even if we conceptually query at slot 1000, the rolling buffer was
    # capped at observe-time, so only the last 150 ever occupy the buffer.
    assert market.quote("pool_A", 50) == 1_000
    assert market.quote("pool_A", 99) == 1_000
    # The low values from slots 0..49 must be gone.
    pcts = market.percentiles("pool_A")
    assert all(v == 1_000 for v in pcts.values())
