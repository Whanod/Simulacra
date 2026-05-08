"""Rolling blockhash history for Solana-mainnet-style ~150-slot expiry.

PRD US-014 line 1101. Solana validators reject a transaction whose
``recent_blockhash`` is older than ~150 slots. This module is the
engine-side rolling window the admit-time expiry check (PRD line 1108)
queries.
"""

from __future__ import annotations

from collections import deque

from defi_sim.core.types import BlockHash

__all__ = [
    "BLOCKHASH_VALIDITY_SLOTS",
    "BlockhashHistory",
]

# PRD US-014 line 1089: Solana mainnet's ~150-slot blockhash validity window.
BLOCKHASH_VALIDITY_SLOTS = 150


class BlockhashHistory:
    """Bounded rolling map of recorded blockhashes keyed by slot.

    The engine calls :meth:`record` once per slot as it advances. Older
    entries are evicted as soon as ``current_slot - oldest_slot >
    validity_slots``, keeping the map bounded at roughly
    ``validity_slots`` entries.
    """

    def __init__(self, validity_slots: int = BLOCKHASH_VALIDITY_SLOTS) -> None:
        self._validity_slots = validity_slots
        self._slot_of: dict[BlockHash, int] = {}
        self._order: deque[BlockHash] = deque()

    @property
    def validity_slots(self) -> int:
        return self._validity_slots

    def record(self, slot: int, blockhash: BlockHash) -> None:
        """Record ``blockhash`` as the blockhash for ``slot``.

        Re-recording an already-known blockhash is a no-op (the original
        slot is preserved). Eviction is driven by ``slot``: any recorded
        blockhash whose slot is more than ``validity_slots`` older than
        ``slot`` is dropped.
        """
        if blockhash not in self._slot_of:
            self._slot_of[blockhash] = slot
            self._order.append(blockhash)
        while self._order:
            oldest = self._order[0]
            oldest_slot = self._slot_of[oldest]
            if slot - oldest_slot > self._validity_slots:
                self._order.popleft()
                del self._slot_of[oldest]
            else:
                break

    def latest(self) -> BlockHash:
        """Return the most recently recorded blockhash.

        Raises ``LookupError`` if no blockhashes have been recorded yet.
        """
        if not self._order:
            raise LookupError("BlockhashHistory is empty")
        return self._order[-1]

    def slot_of(self, blockhash: BlockHash) -> int | None:
        """Return the slot at which ``blockhash`` was recorded, or ``None``."""
        return self._slot_of.get(blockhash)

    def is_expired(self, blockhash: BlockHash | None, current_slot: int) -> bool:
        """Return True if ``blockhash`` is outside the validity window.

        Blockhash-only check (PRD US-014 line 1101). Per-action
        ``expiry_slot`` (PRD line 1097) is enforced by the live admit-time
        predicate in ``SolanaLikeExecution._is_blockhash_expired``, not
        here — callers wanting the action-level test should use that.

        - ``None`` is interpreted as "use latest" (PRD US-014 line 1098)
          and is never expired — the engine resolves it at admit-time.
        - An unknown blockhash (never recorded, or evicted past the
          window) is treated as expired.
        """
        if blockhash is None:
            return False
        slot = self._slot_of.get(blockhash)
        if slot is None:
            return True
        return current_slot - slot > self._validity_slots

    def __len__(self) -> int:
        return len(self._order)
