"""Slot-signal extraction + per-category scoring (FIX-019).

Given a freshly-pulled ``SlotSnapshot``, extract enough signals to decide
whether the slot qualifies for the ``steady_state`` baseline category.
Signals come from the raw block payload + the materializer; nothing is
fetched from external sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_solana.replay.materialize import (
    MaterializedSwapAction,
    TipAction,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot

from .categories import CategoryThresholds, DEFAULT_THRESHOLDS, StressCategory

__all__ = [
    "CategoryScore",
    "SlotSignals",
    "extract_signals",
    "score_for_category",
]


_TIP_ACCOUNTS = frozenset(DEFAULT_JITO_TIP_ACCOUNTS)


@dataclass(frozen=True)
class SlotSignals:
    """Quantitative signals extracted from a single slot at slot time."""

    slot: int
    tx_count: int
    total_compute_units: int
    tip_count: int = 0
    decoded_swap_count: int = 0


@dataclass(frozen=True)
class CategoryScore:
    """Result of scoring one slot against one stress category."""

    slot: int
    category: StressCategory
    qualifies: bool
    score: float
    reason: str


def extract_signals(snapshot: SlotSnapshot) -> SlotSignals:
    """Compute :class:`SlotSignals` from a :class:`SlotSnapshot`.

    Calls :func:`materialize_slot` once to amortize parsing across the
    decoded-action signals.
    """
    actions = materialize_slot(snapshot)

    tip_count = 0
    decoded_swap_count = 0
    for action in actions:
        if isinstance(action, TipAction):
            tip_count += 1
        elif isinstance(action, MaterializedSwapAction):
            decoded_swap_count += 1

    if tip_count == 0:
        # Materializer rejects ill-formed tip transactions; double-check
        # against the raw block so a corrupt parsed payload does not flip
        # a busy slot into "steady state".
        tip_count = _count_raw_tips(snapshot)

    return SlotSignals(
        slot=snapshot.slot,
        tx_count=len(snapshot.transactions),
        total_compute_units=sum(snapshot.transaction_compute_units),
        tip_count=tip_count,
        decoded_swap_count=decoded_swap_count,
    )


def score_for_category(
    signals: SlotSignals,
    category: StressCategory,
    thresholds: CategoryThresholds = DEFAULT_THRESHOLDS,
) -> CategoryScore:
    """Score ``signals`` against ``category`` using ``thresholds``."""
    scorer = _SCORERS[category]
    return scorer(signals, thresholds)


def _score_steady_state(
    signals: SlotSignals, thresholds: CategoryThresholds
) -> CategoryScore:
    qualifies = (
        signals.tx_count <= thresholds.steady_state_max_tx_count
        and signals.total_compute_units <= thresholds.steady_state_max_total_cu
        and signals.tip_count <= thresholds.steady_state_max_tip_count
        and signals.decoded_swap_count <= thresholds.steady_state_max_decoded_swaps
    )
    reason = (
        f"tx_count={signals.tx_count} (<= {thresholds.steady_state_max_tx_count}), "
        f"total_cu={signals.total_compute_units} "
        f"(<= {thresholds.steady_state_max_total_cu}), "
        f"tip_count={signals.tip_count} (<= {thresholds.steady_state_max_tip_count}), "
        f"decoded_swaps={signals.decoded_swap_count} "
        f"(<= {thresholds.steady_state_max_decoded_swaps})"
    )
    score = max(0.0, float(thresholds.steady_state_max_tx_count - signals.tx_count))
    return CategoryScore(
        slot=signals.slot,
        category=StressCategory.STEADY_STATE,
        qualifies=qualifies,
        score=score,
        reason=reason,
    )


_SCORERS = {
    StressCategory.STEADY_STATE: _score_steady_state,
}


def _count_raw_tips(snapshot: SlotSnapshot) -> int:
    """Count system-program transfers to a Jito tip account (raw fallback)."""
    import base64

    count = 0
    for tx in snapshot.transactions:
        if not isinstance(tx, dict):
            continue
        message = tx.get("message")
        if not isinstance(message, dict):
            message = (tx.get("transaction") or {}).get("message") or {}
        if not isinstance(message, dict):
            continue
        account_keys = _flat_account_keys(message)
        for ix in message.get("instructions") or ():
            parsed = ix.get("parsed")
            if isinstance(parsed, dict):
                info = parsed.get("info")
                if (
                    isinstance(info, dict)
                    and parsed.get("type") in ("transfer", "Transfer")
                    and info.get("destination") in _TIP_ACCOUNTS
                ):
                    count += 1
                    continue
            program_id = _resolve_instruction_program(ix, account_keys)
            if program_id != "11111111111111111111111111111111":
                continue
            data = ix.get("data")
            if not isinstance(data, str):
                continue
            try:
                decoded = base64.b64decode(data, validate=False)
            except (ValueError, TypeError):
                continue
            if len(decoded) < 12:
                continue
            tag = int.from_bytes(decoded[:4], "little")
            if tag != 2:
                continue
            accounts = ix.get("accounts") or ()
            if len(accounts) < 2 or not isinstance(accounts[1], int):
                continue
            if accounts[1] >= len(account_keys):
                continue
            if account_keys[accounts[1]] in _TIP_ACCOUNTS:
                count += 1
    return count


def _flat_account_keys(message: dict) -> tuple[str, ...]:
    raw_keys = message.get("accountKeys") or ()
    flat: list[str] = []
    for key in raw_keys:
        if isinstance(key, str):
            flat.append(key)
        elif isinstance(key, dict):
            pubkey = key.get("pubkey")
            if isinstance(pubkey, str):
                flat.append(pubkey)
    loaded_addresses = message.get("loadedAddresses") or {}
    for bucket in ("writable", "readonly"):
        for entry in loaded_addresses.get(bucket) or ():
            if isinstance(entry, str):
                flat.append(entry)
    return tuple(flat)


def _resolve_instruction_program(
    ix: dict, account_keys: tuple[str, ...]
) -> str | None:
    program_id = ix.get("programId")
    if isinstance(program_id, str) and program_id:
        return program_id
    idx = ix.get("programIdIndex")
    if isinstance(idx, int) and 0 <= idx < len(account_keys):
        return account_keys[idx]
    return None
