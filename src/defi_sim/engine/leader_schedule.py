"""Stake-weighted leader schedule for SolanaSlotClock.

Per-epoch schedules are deterministic given a seed and cached after
first computation. The current implementation uses simple per-slot
weighted-random selection; mainnet uses a more involved schedule with
4-slot leader runs.

# TODO calibrate to mainnet schedule per 2.1
"""

from __future__ import annotations

import bisect
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from defi_sim.agents.validator import Validator


@dataclass(frozen=True)
class ValidatorStake:
    pubkey: str
    stake_lamports: int


class LeaderSchedule:
    def __init__(
        self,
        validators: list[ValidatorStake],
        seed: int = 0,
        epoch_length_slots: int = 432_000,
    ):
        if not validators:
            raise ValueError("LeaderSchedule requires at least one validator")
        if epoch_length_slots <= 0:
            raise ValueError(
                f"epoch_length_slots must be positive, got {epoch_length_slots}"
            )
        self._validators = list(validators)
        self._seed = seed
        self._epoch_length_slots = epoch_length_slots

        active = [v for v in self._validators if v.stake_lamports > 0]
        if not active:
            raise ValueError(
                "LeaderSchedule requires at least one validator with non-zero stake"
            )
        self._active = active
        self._pubkeys = [v.pubkey for v in active]
        self._known_pubkeys = {v.pubkey for v in self._validators}

        cum: list[int] = []
        total = 0
        for v in active:
            total += v.stake_lamports
            cum.append(total)
        self._cum = cum
        self._total_stake = total

        self._epoch_cache: dict[int, list[str]] = {}

    @classmethod
    def from_validator_agents(
        cls,
        validators: Iterable["Validator"],
        seed: int = 0,
        epoch_length_slots: int = 432_000,
    ) -> "LeaderSchedule":
        """Build a stake-weighted schedule from `Validator` agents (PRD US-012 line 963).

        Derives the `ValidatorStake` list from each agent's
        `params.stake_lamports` / `params.pubkey`. The primitive
        `LeaderSchedule(validators=[ValidatorStake(...)])` constructor stays
        available for tests and standalone scenarios.
        """
        stakes = [
            ValidatorStake(
                pubkey=v.params.pubkey,
                stake_lamports=v.params.stake_lamports,
            )
            for v in validators
        ]
        return cls(
            validators=stakes,
            seed=seed,
            epoch_length_slots=epoch_length_slots,
        )

    @property
    def epoch_length_slots(self) -> int:
        return self._epoch_length_slots

    @property
    def validators(self) -> list[ValidatorStake]:
        return list(self._validators)

    def _schedule_for_epoch(self, epoch: int) -> list[str]:
        cached = self._epoch_cache.get(epoch)
        if cached is not None:
            return cached
        rng = random.Random(f"{self._seed}:{epoch}")
        leaders: list[str] = []
        cum = self._cum
        pubkeys = self._pubkeys
        total = self._total_stake
        last_idx = len(pubkeys) - 1
        for _ in range(self._epoch_length_slots):
            r = rng.random() * total
            idx = bisect.bisect_left(cum, r)
            if idx > last_idx:
                idx = last_idx
            leaders.append(pubkeys[idx])
        self._epoch_cache[epoch] = leaders
        return leaders

    def leader_for_slot(self, slot: int) -> str:
        if slot < 0:
            raise ValueError(f"slot must be non-negative, got {slot}")
        epoch = slot // self._epoch_length_slots
        offset = slot % self._epoch_length_slots
        return self._schedule_for_epoch(epoch)[offset]

    def next_leader_slot(
        self,
        pubkey: str,
        after_slot: int,
        max_epochs_to_search: int = 4,
    ) -> int:
        if pubkey not in self._known_pubkeys:
            raise ValueError(f"Unknown validator pubkey: {pubkey!r}")
        slot = after_slot + 1
        if slot < 0:
            slot = 0
        start_epoch = slot // self._epoch_length_slots
        for epoch in range(start_epoch, start_epoch + max_epochs_to_search):
            schedule = self._schedule_for_epoch(epoch)
            base = epoch * self._epoch_length_slots
            start_off = max(0, slot - base)
            for off in range(start_off, self._epoch_length_slots):
                if schedule[off] == pubkey:
                    return base + off
        raise RuntimeError(
            f"No leader slot for {pubkey!r} found within "
            f"{max_epochs_to_search} epochs after slot {after_slot}"
        )
