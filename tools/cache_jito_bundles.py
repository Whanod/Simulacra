"""Capture real Jito bundles from mainnet for landing-rate calibration (FIX-020).

Walks recent finalized slots backward from ``getSlot``, identifies tip
transfers in each block, groups consecutive same-payer transactions ending
in a tip transfer into Jito bundles, attributes writable accounts (resolving
ALT entries via ``meta.loadedAddresses``), and emits one row per bundle to
``solana-plans/calibration/corpus/jito_bundles/<YYYY-MM-DD>/bundles.jsonl.gz``.

The captured corpus feeds two calibration surfaces:

1. ``BundleAuction.tip_quote`` — empirical CDF of in-cohort tip lamports as
   the prior the auction blends against in-process observations.
2. ``SubmissionPathPriors.jito_relayer_landing_prob_baseline`` — the share
   of bundles that did not need to be re-attempted in adjacent slots.

Resumability is built in: progress is checkpointed to ``_progress.json`` in
the output directory after every flush. Re-running with the same ``--out``
resumes from the last captured slot. Pass ``--restart`` to ignore the
checkpoint and start fresh.

Usage::

    python tools/cache_jito_bundles.py \\
        --slots 1500 \\
        --cohort Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE,EUuUbDcafPrmVTD5M6qoJAoyyNbihBhugADAxRMn5he9,2WLWEuKDgkDUccTpbwYp1GToYktiSB1cXvreHUwiSUVP \\
        --out solana-plans/calibration/corpus/jito_bundles/2026-05-05/

The default cohort is the lighthouse SOL/USDC Whirlpool (pool + both vaults).
``--concurrency`` defaults to 4 inflight ``getBlock`` requests so the run
stays well under typical paid-tier rate caps.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from defi_sim.engine.bundle import MAX_BUNDLE_TXS
from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_solana.replay.slot_client import (
    JsonRpcSolanaClient,
    SolanaClient,
    default_client,
)

__all__ = [
    "BundleRow",
    "DEFAULT_LIGHTHOUSE_COHORT",
    "capture_bundles",
    "extract_bundles_from_block",
    "main",
]


# Lighthouse SOL/USDC Whirlpool cohort: pool account + both SPL token vaults.
# Matches the corpus fixture committed under
# solana-plans/calibration/corpus/420196842/ (canonical 4 bps tier).
DEFAULT_LIGHTHOUSE_COHORT: tuple[str, ...] = (
    "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
    "EUuUbDcafPrmVTD5M6qoJAoyyNbihBhugADAxRMn5he9",
    "2WLWEuKDgkDUccTpbwYp1GToYktiSB1cXvreHUwiSUVP",
)

_SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
_TIP_ACCOUNT_SET = frozenset(DEFAULT_JITO_TIP_ACCOUNTS)


@dataclass(frozen=True, slots=True)
class BundleRow:
    """One Jito bundle observed in a finalized slot.

    ``any_tx_reverted`` is True when at least one transaction in the bundle
    landed with ``meta.err`` set. Jito bundles have all-or-nothing execution
    semantics, but the *tip* signal is informative regardless of bundle
    success — it represents what the searcher was willing to pay. The fitter
    can choose to weight reverted bundles differently when computing
    percentiles or use the reverted-share as a landing-rate proxy.
    """

    slot: int
    bundle_index_in_slot: int
    payer: str
    tip_lamports: int
    writable_accounts: tuple[str, ...]
    is_in_cohort: bool
    tx_count: int
    block_time: int | None
    first_signature: str | None
    any_tx_reverted: bool = False

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "bundle_index_in_slot": self.bundle_index_in_slot,
            "payer": self.payer,
            "tip_lamports": self.tip_lamports,
            "writable_accounts": list(self.writable_accounts),
            "is_in_cohort": self.is_in_cohort,
            "tx_count": self.tx_count,
            "block_time": self.block_time,
            "first_signature": self.first_signature,
            "any_tx_reverted": self.any_tx_reverted,
        }


# ── tx parsing helpers ───────────────────────────────────────────────────

def _flat_account_keys(message: dict, meta: dict | None) -> tuple[list[str], int, int, int]:
    """Return (all_keys, num_signed, num_static, num_writable_signed).

    Where ``all_keys`` is in the canonical Solana ordering:
      [signed-writable] + [signed-readonly] +
      [unsigned-writable static] + [unsigned-readonly static] +
      [loadedAddresses.writable] + [loadedAddresses.readonly]

    ``num_static`` is the count of static keys (the part of ``all_keys``
    before the loadedAddresses tail). ``num_writable_signed`` and
    ``num_signed`` lets callers reconstruct writable-ness for static keys.
    Loaded-addresses writability is derived from the ``writable`` bucket.
    """
    raw_keys = message.get("accountKeys") or ()
    static: list[str] = []
    for key in raw_keys:
        if isinstance(key, str):
            static.append(key)
        elif isinstance(key, dict):
            pubkey = key.get("pubkey")
            if isinstance(pubkey, str):
                static.append(pubkey)

    header = message.get("header") or {}
    num_signed = int(header.get("numRequiredSignatures") or 0)
    num_readonly_signed = int(header.get("numReadonlySignedAccounts") or 0)
    num_writable_signed = max(0, num_signed - num_readonly_signed)

    loaded = (meta or {}).get("loadedAddresses") or {}
    writable_loaded = [k for k in (loaded.get("writable") or ()) if isinstance(k, str)]
    readonly_loaded = [k for k in (loaded.get("readonly") or ()) if isinstance(k, str)]

    all_keys = list(static) + writable_loaded + readonly_loaded
    return all_keys, num_signed, len(static), num_writable_signed


def _writable_indices(num_static: int, num_signed: int, num_writable_signed: int, header: dict) -> set[int]:
    """Indices of writable static keys per Solana message header semantics."""
    num_readonly_unsigned = int(header.get("numReadonlyUnsignedAccounts") or 0)
    writable: set[int] = set()
    # signed-writable: [0, num_writable_signed)
    for i in range(num_writable_signed):
        writable.add(i)
    # unsigned-writable: [num_signed, num_static - num_readonly_unsigned)
    end = num_static - num_readonly_unsigned
    for i in range(num_signed, max(num_signed, end)):
        writable.add(i)
    return writable


def _resolve_program_id(ix: dict, account_keys: Sequence[str]) -> str | None:
    program_id = ix.get("programId")
    if isinstance(program_id, str) and program_id:
        return program_id
    idx = ix.get("programIdIndex")
    if isinstance(idx, int) and 0 <= idx < len(account_keys):
        return account_keys[idx]
    return None


def _instruction_tip_amount(
    ix: dict, account_keys: Sequence[str]
) -> int | None:
    """Return tip lamports if ``ix`` is a system-program transfer to a tip account.

    Handles both jsonParsed (``parsed.type == 'transfer'``) and raw json (base58
    or base64-encoded ``data`` with the SystemProgram::Transfer instruction tag).
    """
    parsed = ix.get("parsed")
    if isinstance(parsed, dict):
        info = parsed.get("info")
        if (
            isinstance(info, dict)
            and parsed.get("type") in ("transfer", "Transfer")
            and info.get("destination") in _TIP_ACCOUNT_SET
        ):
            try:
                return int(info.get("lamports") or 0)
            except (TypeError, ValueError):
                return None
        return None

    program_id = _resolve_program_id(ix, account_keys)
    if program_id != _SYSTEM_PROGRAM_ID:
        return None
    data = ix.get("data")
    if not isinstance(data, str):
        return None
    decoded = _decode_ix_data(data)
    if decoded is None or len(decoded) < 12:
        return None
    tag = int.from_bytes(decoded[:4], "little")
    if tag != 2:  # SystemProgram::Transfer
        return None
    accounts = ix.get("accounts") or ()
    if len(accounts) < 2 or not isinstance(accounts[1], int):
        return None
    dest_idx = accounts[1]
    if dest_idx >= len(account_keys):
        return None
    if account_keys[dest_idx] not in _TIP_ACCOUNT_SET:
        return None
    return int.from_bytes(decoded[4:12], "little")


def _decode_ix_data(data: str) -> bytes | None:
    """Decode ``data`` as base58 (default) or base64.

    ``getBlock(encoding=json)`` returns base58-encoded instruction data;
    ``encoding=base64`` returns base64. We try base58 first (Helius's default
    for ``encoding=json``) then fall back to base64.
    """
    try:
        return _b58decode(data)
    except ValueError:
        pass
    try:
        return base64.b64decode(data, validate=False)
    except (ValueError, TypeError):
        return None


_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    """Stdlib-only base58 decode (Bitcoin alphabet)."""
    if not s:
        return b""
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    n = 0
    for ch in s:
        idx = _B58_ALPHABET.find(ch.encode("ascii"))
        if idx < 0:
            raise ValueError(f"non-base58 character {ch!r}")
        n = n * 58 + idx
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body


def _tx_meta(tx: dict) -> dict:
    meta = tx.get("meta")
    return meta if isinstance(meta, dict) else {}


def _tx_message(tx: dict) -> dict | None:
    transaction = tx.get("transaction")
    if isinstance(transaction, dict):
        message = transaction.get("message")
        if isinstance(message, dict):
            return message
    message = tx.get("message")
    if isinstance(message, dict):
        return message
    return None


def _tx_first_signature(tx: dict) -> str | None:
    transaction = tx.get("transaction")
    sigs: Sequence[Any] | None = None
    if isinstance(transaction, dict):
        raw_sigs = transaction.get("signatures")
        if isinstance(raw_sigs, list):
            sigs = raw_sigs
    if sigs is None:
        raw_sigs = tx.get("signatures")
        if isinstance(raw_sigs, list):
            sigs = raw_sigs
    if sigs and isinstance(sigs[0], str):
        return sigs[0]
    return None


def _scan_tx_tips_and_writes(
    tx: dict,
) -> tuple[int, set[str], str | None, bool]:
    """Return (tip_lamports, writable_accounts, payer, reverted) for one tx.

    Walks top-level + inner instructions to pick up tip transfers regardless
    of whether the bundle wraps the tip in a CPI. Reverted transactions
    (``meta.err is not None``) still contribute their writable-account set
    and bid lamports because Jito's tip transfer is typically a
    SystemProgram::Transfer ordered before the failing instruction — the
    searcher's *willingness to pay* is informative for percentile fitting
    even when the larger bundle later reverted. The ``reverted`` flag is
    surfaced so downstream fitters can filter or weight accordingly.
    """
    meta = _tx_meta(tx)
    reverted = meta.get("err") is not None
    message = _tx_message(tx)
    if message is None:
        return 0, set(), None, reverted

    all_keys, num_signed, num_static, num_writable_signed = _flat_account_keys(
        message, meta
    )
    if not all_keys:
        return 0, set(), None, reverted
    payer = all_keys[0]

    # Writable static keys (header-driven).
    header = message.get("header") or {}
    writable_static = _writable_indices(
        num_static=num_static,
        num_signed=num_signed,
        num_writable_signed=num_writable_signed,
        header=header,
    )
    writable_keys: set[str] = {
        all_keys[i] for i in writable_static if i < num_static
    }
    # Writable loaded-addresses keys (always writable bucket).
    loaded = meta.get("loadedAddresses") or {}
    for k in loaded.get("writable") or ():
        if isinstance(k, str):
            writable_keys.add(k)

    tip_lamports = 0
    for ix in message.get("instructions") or ():
        amount = _instruction_tip_amount(ix, all_keys)
        if amount:
            tip_lamports += int(amount)
    # Inner instructions land under meta.innerInstructions[*].instructions.
    for inner in meta.get("innerInstructions") or ():
        if not isinstance(inner, dict):
            continue
        for ix in inner.get("instructions") or ():
            amount = _instruction_tip_amount(ix, all_keys)
            if amount:
                tip_lamports += int(amount)

    return tip_lamports, writable_keys, payer, reverted


# ── bundle grouping ─────────────────────────────────────────────────────

def extract_bundles_from_block(
    block: dict,
    *,
    cohort: frozenset[str],
    slot: int,
    max_bundle_txs: int = MAX_BUNDLE_TXS,
) -> list[BundleRow]:
    """Walk slot transactions and emit one ``BundleRow`` per Jito bundle.

    Heuristic (per Jito's on-chain bundle layout): a bundle is a contiguous
    run of up to ``max_bundle_txs`` transactions terminating in a tip
    transfer. Walks txs in execution order; for each tip-paying tx, the
    bundle is the trailing window of slot transactions ending at the tip,
    capped at ``max_bundle_txs`` and bounded below by the previous bundle's
    terminus. Same-payer is *not* required because Jito bundles can include
    the victim's own transaction (with a different signer) — a sandwich
    bundle has front-run (searcher) + victim (victim) + back-run+tip
    (searcher), and grouping by payer would split it.
    """
    transactions = block.get("transactions") or ()
    block_time = block.get("blockTime") if isinstance(block.get("blockTime"), int) else None

    parsed: list[tuple[int, set[str], str | None, str | None, bool]] = []
    for tx in transactions:
        if not isinstance(tx, dict):
            parsed.append((0, set(), None, None, False))
            continue
        tip, writes, payer, reverted = _scan_tx_tips_and_writes(tx)
        sig = _tx_first_signature(tx)
        parsed.append((tip, writes, payer, sig, reverted))

    bundles: list[BundleRow] = []
    bundle_idx = 0
    boundary = 0  # exclusive lower bound of the current bundle window
    for i, (tip, writes, payer, _sig, _rev) in enumerate(parsed):
        if tip <= 0 or payer is None:
            continue
        # Bundle = up to ``max_bundle_txs`` contiguous txs ending at i,
        # bounded below by the previous bundle's terminus.
        start = max(boundary, i - max_bundle_txs + 1)
        bundle_txs = parsed[start : i + 1]
        merged_writes: set[str] = set()
        any_reverted = False
        for _t, w, _p, _s, rev in bundle_txs:
            merged_writes |= w
            if rev:
                any_reverted = True
        is_in_cohort = bool(merged_writes & cohort)
        first_sig = bundle_txs[0][3]

        bundles.append(
            BundleRow(
                slot=slot,
                bundle_index_in_slot=bundle_idx,
                payer=payer,
                tip_lamports=tip,
                writable_accounts=tuple(sorted(merged_writes)),
                is_in_cohort=is_in_cohort,
                tx_count=len(bundle_txs),
                block_time=block_time,
                first_signature=first_sig,
                any_tx_reverted=any_reverted,
            )
        )
        bundle_idx += 1
        boundary = i + 1
    return bundles


# ── RPC ──────────────────────────────────────────────────────────────────

def _get_latest_finalized_slot(endpoint: str, *, timeout: float = 30.0) -> int:
    """Call ``getSlot {commitment: finalized}`` and return the slot."""
    import urllib.request

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSlot",
        "params": [{"commitment": "finalized"}],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    result = body.get("result")
    if not isinstance(result, int):
        raise RuntimeError(f"getSlot returned non-int result: {body!r}")
    return result


def _safe_get_block(client: SolanaClient, slot: int) -> dict | None:
    """Pull ``getBlock(slot)`` and return ``None`` for skipped/missing slots."""
    try:
        return client.get_block(slot)
    except RuntimeError as exc:
        msg = str(exc).lower()
        # Helius returns RPC error -32004 / -32007 for skipped slots; the
        # default client raises a RuntimeError after exhausting retries.
        # We treat any non-block result as a skip rather than aborting the
        # whole capture.
        if "skipped" in msg or "not available" in msg or "-32007" in msg or "-32004" in msg:
            return None
        raise


# ── persistence ─────────────────────────────────────────────────────────

class _GzipJsonlWriter:
    """Append-friendly gzipped JSONL writer with periodic fsync.

    Writes buffered rows to a fresh ``.partial`` file, then renames over the
    canonical path on each flush, so an interrupted process never leaves a
    truncated gzip stream.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: list[dict[str, Any]] = []
        self._existing: list[bytes] = []
        if path.exists():
            with gzip.open(path, "rb") as fh:
                # Preserve already-captured rows so resume + flush don't
                # truncate prior runs.
                for line in fh:
                    self._existing.append(line)

    def append(self, row: dict[str, Any]) -> None:
        self._rows.append(row)

    def flush(self) -> None:
        if not self._rows:
            return
        tmp = self.path.with_suffix(self.path.suffix + ".partial")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as raw_fh, gzip.GzipFile(
            fileobj=raw_fh, mode="wb", compresslevel=6, mtime=0
        ) as fh:
            for line in self._existing:
                fh.write(line)
            for row in self._rows:
                fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                fh.write(b"\n")
        os.replace(tmp, self.path)
        # Move the just-flushed rows into the existing buffer so subsequent
        # flushes preserve them without re-reading from disk.
        for row in self._rows:
            self._existing.append(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        self._rows.clear()


def _read_progress(out_dir: Path) -> dict[str, Any]:
    progress_path = out_dir / "_progress.json"
    if not progress_path.exists():
        return {}
    try:
        return json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_progress(out_dir: Path, payload: dict[str, Any]) -> None:
    progress_path = out_dir / "_progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = progress_path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(tmp, progress_path)


# ── orchestration ────────────────────────────────────────────────────────

def _iter_target_slots(
    *,
    head_slot: int,
    target_count: int,
    skip_below: int | None,
) -> Iterator[int]:
    slot = head_slot
    emitted = 0
    while emitted < target_count:
        if skip_below is not None and slot <= skip_below:
            return
        yield slot
        slot -= 1
        emitted += 1


def capture_bundles(
    *,
    out_dir: Path,
    cohort: Sequence[str],
    target_slots: int,
    client: SolanaClient | None = None,
    head_slot: int | None = None,
    concurrency: int = 4,
    flush_every: int = 50,
    restart: bool = False,
    log: callable = print,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Capture ``target_slots`` finalized slots backward from ``head_slot``.

    Resumable: ``out_dir/_progress.json`` records the lowest captured slot;
    a re-run continues backward from one slot below it. The captured rows
    accumulate in ``out_dir/bundles.jsonl.gz``; the manifest snapshot lands
    in ``out_dir/manifest.json``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cohort_frozen = frozenset(cohort)

    progress = {} if restart else _read_progress(out_dir)
    bundle_path = out_dir / "bundles.jsonl.gz"
    if restart and bundle_path.exists():
        bundle_path.unlink()

    if client is None:
        client = default_client()
    endpoint = getattr(client, "endpoint", None) or os.environ.get("SOLANA_RPC_URL", "")
    if head_slot is None:
        if progress.get("next_slot") is not None:
            head_slot = int(progress["next_slot"])
        else:
            head_slot = _get_latest_finalized_slot(endpoint) - 50

    floor_slot = progress.get("floor_slot")
    captured_slots = int(progress.get("captured_slots") or 0)
    captured_bundles = int(progress.get("captured_bundles") or 0)
    captured_in_cohort = int(progress.get("captured_in_cohort") or 0)

    writer = _GzipJsonlWriter(bundle_path)

    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    log(
        f"[capture] head_slot={head_slot} target={target_slots} "
        f"concurrency={concurrency} cohort={'|'.join(cohort)}"
    )

    # We capture in a sliding backward scan; because getBlock is the dominant
    # latency, we use a thread pool to fan out N inflight requests at a time.
    # Slot ordering inside the writer is maintained by collecting per-batch
    # results before flushing.
    remaining = target_slots
    next_slot = head_slot
    last_progress_slot = head_slot

    flush_lock = threading.Lock()

    def _process_slot(slot: int) -> tuple[int, list[BundleRow] | None]:
        block = _safe_get_block(client, slot)
        if block is None:
            return slot, None
        rows = extract_bundles_from_block(block, cohort=cohort_frozen, slot=slot)
        return slot, rows

    try:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            while remaining > 0:
                batch_size = min(concurrency * 4, remaining)
                slots_this_batch = []
                for _ in range(batch_size):
                    slots_this_batch.append(next_slot)
                    next_slot -= 1
                results = list(pool.map(_process_slot, slots_this_batch))
                # Sort descending so higher slots are written first, matching
                # the iteration order.
                results.sort(key=lambda x: -x[0])
                for slot, rows in results:
                    if rows is None:
                        continue
                    captured_slots += 1
                    for row in rows:
                        writer.append(row.to_jsonable())
                        captured_bundles += 1
                        if row.is_in_cohort:
                            captured_in_cohort += 1
                    floor_slot = slot if floor_slot is None else min(floor_slot, slot)
                    last_progress_slot = slot
                remaining -= batch_size

                if captured_slots % flush_every < batch_size or remaining <= 0:
                    with flush_lock:
                        writer.flush()
                        _write_progress(
                            out_dir,
                            {
                                "next_slot": next_slot,
                                "floor_slot": floor_slot,
                                "captured_slots": captured_slots,
                                "captured_bundles": captured_bundles,
                                "captured_in_cohort": captured_in_cohort,
                                "cohort": list(cohort),
                                "started_at": started_at,
                                "endpoint_provider_id": _provider_id(endpoint),
                            },
                        )
                    log(
                        f"[capture] floor_slot={floor_slot} "
                        f"slots={captured_slots} bundles={captured_bundles} "
                        f"in_cohort={captured_in_cohort} remaining={remaining}"
                    )
    except KeyboardInterrupt:
        log("[capture] interrupted; flushing partial progress")
    finally:
        writer.flush()

    finished_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    manifest = {
        "captured_at": finished_at,
        "started_at": started_at,
        "head_slot": head_slot,
        "floor_slot": floor_slot,
        "captured_slots": captured_slots,
        "captured_bundles": captured_bundles,
        "captured_in_cohort": captured_in_cohort,
        "cohort": list(cohort),
        "endpoint_provider_id": _provider_id(endpoint),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    _write_progress(
        out_dir,
        {
            **manifest,
            "next_slot": last_progress_slot - 1 if last_progress_slot else None,
        },
    )
    log(
        f"[capture] done: slots={captured_slots} bundles={captured_bundles} "
        f"in_cohort={captured_in_cohort}"
    )
    return manifest


def _provider_id(endpoint: str) -> str:
    """Return a redacted provider id (host only) for the manifest."""
    if not endpoint:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        return parsed.hostname or endpoint
    except Exception:  # pragma: no cover — never crash on identity computation
        return endpoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture real Jito bundles for landing-rate calibration.",
    )
    parser.add_argument(
        "--slots",
        type=int,
        default=1500,
        help="Number of finalized slots to capture (going backward from head).",
    )
    parser.add_argument(
        "--cohort",
        type=str,
        default=",".join(DEFAULT_LIGHTHOUSE_COHORT),
        help=(
            "Comma-separated cohort pubkeys (writable accounts). "
            "Default: lighthouse SOL/USDC Whirlpool pool + both vaults."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output directory. Defaults to "
            "solana-plans/calibration/corpus/jito_bundles/<YYYY-MM-DD>/."
        ),
    )
    parser.add_argument(
        "--head-slot",
        type=int,
        default=None,
        help="Override the head slot to start from (default: latest finalized - 50).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Inflight getBlock requests (paid-tier rate-limit safe at 4).",
    )
    parser.add_argument(
        "--flush-every",
        type=int,
        default=50,
        help="Flush gzipped JSONL output to disk every N captured slots.",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Ignore _progress.json checkpoint and restart capture.",
    )
    args = parser.parse_args(argv)

    cohort = tuple(p.strip() for p in args.cohort.split(",") if p.strip())
    if not cohort:
        parser.error("--cohort must contain at least one pubkey")
    out_dir = args.out
    if out_dir is None:
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        out_dir = Path("solana-plans/calibration/corpus/jito_bundles") / today

    capture_bundles(
        out_dir=out_dir,
        cohort=cohort,
        target_slots=args.slots,
        head_slot=args.head_slot,
        concurrency=args.concurrency,
        flush_every=args.flush_every,
        restart=args.restart,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
