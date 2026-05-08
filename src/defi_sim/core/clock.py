"""Time abstraction for simulation rounds.

Protocols that need elapsed time (interest accrual, TWAP oracles,
vesting schedules) read from the clock rather than treating rounds
as raw time.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod

from defi_sim.core.types import SlotEvent, SlotSkippedEvent

__all__ = [
    "Clock",
    "BlockClock",
    "VariableBlockClock",
    "SolanaSlotClock",
    "SlotEvent",
    "SlotSkippedEvent",
]


class Clock(ABC):
    @abstractmethod
    def timestamp(self, round: int) -> int | float:
        """Return simulated Unix timestamp for the given round.
        Solana's 0.4 s slot time means downstream consumers must
        tolerate fractional seconds; integer-clock implementations
        are still permitted to return ints."""
        ...

    @abstractmethod
    def elapsed(self, from_round: int, to_round: int) -> int | float:
        """Return elapsed seconds between two rounds."""
        ...

    @abstractmethod
    def epoch(self, round: int) -> int:
        """Return the epoch index for the given round.
        Epochs group rounds for periodic operations (staking rewards,
        interest compounding, validator rotation). Default: every round
        is its own epoch."""
        ...


class BlockClock(Clock):
    """Maps rounds to timestamps using a fixed block time.
    Default: 1 second per round in network-neutral mode."""

    def __init__(self, genesis: int = 0, block_time: int = 1, epoch_length: int = 1):
        self.genesis = genesis
        self.block_time = block_time
        self.epoch_length = epoch_length

    def timestamp(self, round: int) -> int:
        return self.genesis + round * self.block_time

    def elapsed(self, from_round: int, to_round: int) -> int:
        return (to_round - from_round) * self.block_time

    def epoch(self, round: int) -> int:
        return round // self.epoch_length


class VariableBlockClock(Clock):
    """Replays real block timestamps from a sequence.
    Useful for backtesting against historical chain data."""

    def __init__(self, timestamps: list[int], epoch_length: int = 1):
        """timestamps: one Unix timestamp per round, must be monotonically increasing."""
        self._timestamps = timestamps
        self.epoch_length = epoch_length

    def timestamp(self, round: int) -> int:
        return self._timestamps[round]

    def elapsed(self, from_round: int, to_round: int) -> int:
        return self._timestamps[to_round] - self._timestamps[from_round]

    def epoch(self, round: int) -> int:
        return round // self.epoch_length


class SolanaSlotClock(Clock):
    """Solana-native slot clock.

    One simulation round == one slot. Slots advance at
    `slot_duration_seconds` (default 0.4 s). With probability
    `skip_rate`, `tick()` emits a `SlotSkippedEvent` instead of a
    normal `SlotEvent`; the slot counter still advances either way
    so simulated wall-clock time keeps moving.
    """

    def __init__(
        self,
        slot_duration_seconds: float = 0.4,
        epoch_length_slots: int = 432_000,
        skip_rate: float = 0.0,
        genesis: int = 0,
        seed: int | None = None,
    ):
        if not 0.0 <= skip_rate <= 1.0:
            raise ValueError(f"skip_rate must be in [0, 1], got {skip_rate}")
        self.slot_duration_seconds = slot_duration_seconds
        self.epoch_length_slots = epoch_length_slots
        self.skip_rate = skip_rate
        self.genesis = genesis
        self.current_slot: int = 0
        self._rng = random.Random(seed)

    def tick(self) -> SlotEvent | SlotSkippedEvent:
        self.current_slot += 1
        if self.skip_rate > 0.0 and self._rng.random() < self.skip_rate:
            return SlotSkippedEvent(slot=self.current_slot, scheduled_leader=None)
        return SlotEvent(slot=self.current_slot)

    @property
    def current_epoch(self) -> int:
        return self.current_slot // self.epoch_length_slots

    def timestamp(self, round: int) -> float:
        return self.genesis + round * self.slot_duration_seconds

    def elapsed(self, from_round: int, to_round: int) -> float:
        return (to_round - from_round) * self.slot_duration_seconds

    def epoch(self, round: int) -> int:
        return round // self.epoch_length_slots
