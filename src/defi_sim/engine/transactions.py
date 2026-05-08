"""Solana versioned-transaction envelope.

Wraps inner ``Action`` instructions with the metadata Solana needs to validate
and price a tx: required-signer count, optional ALT references, and the recent
blockhash (wired by US-019 / 1.12). The wrapper is what gets *submitted*; the
inner ``actions`` are what the engine *executes*.

See PRD US-009 (line 648) for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from defi_sim.core.types import Action, BlockHash
from defi_sim.engine.scheduler import AccountId

AltId = str

__all__ = [
    "AddressLookupTable",
    "AltId",
    "BlockHash",
    "MAX_TX_SIZE_BYTES",
    "VersionedTransaction",
    "compute_tx_size",
]

# PRD US-009 line 675: Solana wire-format hard cap on a single packet
# (Solana's 1232-byte MTU = MAX_PACKET_DATA_SIZE). Anything above this is
# rejected at admit time with ``DropReason.TX_SIZE_EXCEEDED``.
MAX_TX_SIZE_BYTES = 1232


@dataclass
class VersionedTransaction:
    actions: list[Action]
    lookup_tables: list[AltId] = field(default_factory=list)
    num_required_signatures: int = 1
    recent_blockhash: BlockHash | None = None
    # PRD US-014 line 1096: ``None`` means "blockhash_slot + 150" (Solana's
    # ~150-slot blockhash validity window).
    expiry_slot: int | None = None


@dataclass
class AddressLookupTable:
    id: AltId
    entries: list[AccountId]


def compute_tx_size(
    versioned_tx: VersionedTransaction,
    alts: Mapping[AltId, AddressLookupTable] | None = None,
) -> int:
    """Wire-format size in bytes for ``versioned_tx``.

    Implements the PRD US-009 spec at line 666:

    - 1 signature-count header byte + ``num_required_signatures * 64`` sig bytes.
    - 3 message-header bytes.
    - For each unique account referenced by inner actions
      (``read_locks | write_locks``): 3 bytes if the account is covered by any
      ALT in ``versioned_tx.lookup_tables`` (table-index + account-index + flag),
      otherwise 32 bytes (raw pubkey).
    - Sum of per-instruction encoded sizes (program_id ref + data).

    Inner actions expose their account references via duck-typed
    ``read_locks`` / ``write_locks`` (compatible with ``LockedAction``); a bare
    ``Action`` without those attributes contributes no account references.

    # CALIBRATE-2.1: per-instruction ``data`` length defaults to 0 unless the
    # action exposes a ``data`` attribute. Replace with a per-action-type
    # data-byte registry once real instruction-data emission lands.
    """
    alts_registry = alts or {}

    size = 1 + versioned_tx.num_required_signatures * 64
    size += 3

    accounts: set[AccountId] = set()
    for action in versioned_tx.actions:
        accounts.update(getattr(action, "read_locks", frozenset()))
        accounts.update(getattr(action, "write_locks", frozenset()))

    alt_resolved: set[AccountId] = set()
    for alt_id in versioned_tx.lookup_tables:
        alt = alts_registry.get(alt_id)
        if alt is not None:
            alt_resolved.update(alt.entries)

    for account in accounts:
        size += 3 if account in alt_resolved else 32

    for action in versioned_tx.actions:
        program_id_ref = 1
        data_bytes = len(getattr(action, "data", b""))
        size += program_id_ref + data_bytes

    return size
