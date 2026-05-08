"""LeaderSchedule synthetic-epoch integration test (PRD US-001).

Replays a full 432_000-slot epoch against a hand-constructed validator
list and asserts per-validator leader-slot counts fall within tolerance
of the configured stake distribution.

This is a *synthetic* distribution test — it locks the stake-weighted
selection mechanic over a full epoch. Exact mainnet leader-pubkey
replay is out of scope here; see the ``# CALIBRATE-2.1`` marker in
PRD.md for the Phase 2 archival-corpus replay.
"""

from __future__ import annotations

from collections import Counter

from defi_sim.engine.leader_schedule import LeaderSchedule, ValidatorStake


def test_synthetic_epoch_matches_configured_stake_distribution() -> None:
    epoch_length = 432_000
    validators = [
        ValidatorStake(pubkey="A", stake_lamports=30),
        ValidatorStake(pubkey="B", stake_lamports=25),
        ValidatorStake(pubkey="C", stake_lamports=20),
        ValidatorStake(pubkey="D", stake_lamports=15),
        ValidatorStake(pubkey="E", stake_lamports=10),
    ]
    total_stake = sum(v.stake_lamports for v in validators)

    schedule = LeaderSchedule(validators, seed=2026, epoch_length_slots=epoch_length)

    counts: Counter[str] = Counter()
    for slot in range(epoch_length):
        counts[schedule.leader_for_slot(slot)] += 1

    assert sum(counts.values()) == epoch_length

    # ±1% absolute share tolerance; for n=432_000 the worst-case binomial
    # σ (at p=0.5) is ~329 slots — ±1% = 4_320 slots is well over 10σ.
    for v in validators:
        target_share = v.stake_lamports / total_stake
        observed_share = counts[v.pubkey] / epoch_length
        assert abs(observed_share - target_share) < 0.01, (
            f"validator {v.pubkey}: observed share {observed_share:.4f} "
            f"diverges from target {target_share:.4f} by more than 1%"
        )
