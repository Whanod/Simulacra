"""Solana JSON-RPC compatibility shim.

This route is intentionally a translation layer. The simulation work is owned
by US-005's bundle simulator route; `simulateTransaction` converts a single
transaction into a one-transaction bundle and delegates to
`simulate_bundle_internal`.
"""

from __future__ import annotations

import base64
import copy
from typing import Any

from fastapi import APIRouter, HTTPException

from defi_sim_api.routers import simulate_bundle
from defi_sim_solana.replay.account_client import AccountRecord, AccountSnapshot
from defi_sim_solana.replay.corpus import corpus_root, load_corpus_fixture
from defi_sim_solana.replay.slot_client import SlotSnapshot

router = APIRouter(prefix="/solana-rpc", tags=["solana-rpc"])

_JSONRPC_VERSION = "2.0"
_DEFAULT_TIP_RECIPIENT = "JsonRpcSimulateTransaction11111111111111111111111"
_DEFAULT_BLOCKHASH = "DeFiSim111111111111111111111111111111111111"
_LAMPORTS_PER_SIGNATURE = 5_000
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_WRITE_OR_SIGNING_METHODS = {
    "sendTransaction",
    "sendRawTransaction",
    "signMessage",
    "signTransaction",
    "signAllTransactions",
}

_CURRENT_SLOT: int | None = None
_SLOT_CACHE: dict[int, SlotSnapshot] = {}
_ACCOUNT_SNAPSHOT_CACHE: dict[tuple[int, str], AccountSnapshot] = {}
_TX_CACHE: dict[str, tuple[int, dict[str, Any], SlotSnapshot]] = {}


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "error": error}


def _extract_simulate_transaction_request(
    params: Any,
) -> simulate_bundle.SimulateBundleRequest:
    if not isinstance(params, list) or not params:
        raise ValueError("simulateTransaction params must be [transaction, options?]")
    tx = params[0]
    if not isinstance(tx, str) or not tx:
        raise ValueError("simulateTransaction transaction must be a non-empty string")

    options = params[1] if len(params) > 1 else {}
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("simulateTransaction options must be an object")

    context_slot = options.get("contextSlot", "latest")
    tip_lamports = int(options.get("tipLamports", 0))
    tip_recipient = str(options.get("tipRecipient", _DEFAULT_TIP_RECIPIENT))

    return simulate_bundle.SimulateBundleRequest(
        bundle=simulate_bundle.BundleRequestModel(
            txs=[tx],
            tip_lamports=tip_lamports,
            tip_recipient=tip_recipient,
        ),
        context_slot=context_slot,
        fork_spec=None,
        search_tip_optimizer=None,
    )


def _simulate_transaction(params: Any) -> dict[str, Any]:
    request = _extract_simulate_transaction_request(params)
    result = simulate_bundle.simulate_bundle_internal(request)
    units_consumed = sum(result.cu_budget.tx_cu_used)
    context_slot = (
        request.context_slot
        if isinstance(request.context_slot, int)
        else _current_slot()
    )
    if isinstance(request.context_slot, int):
        _set_current_slot(request.context_slot)
    return {
        "context": {"slot": context_slot},
        "value": {
            "err": None,
            "logs": [
                "Program defi-sim invoke [1]",
                (f"Program log: landing_probability={result.landing_probability:.6f}"),
                (
                    "Program log: expected_tip_to_land_lamports="
                    f"{result.expected_tip_to_land_lamports}"
                ),
                "Program defi-sim success",
            ],
            "accounts": _simulate_transaction_accounts(params, context_slot),
            "unitsConsumed": units_consumed,
            "returnData": None,
        },
    }


def _simulate_transaction_accounts(
    params: Any, slot: int
) -> list[dict[str, Any] | None] | None:
    values = _params_list("simulateTransaction", params)
    options = values[1] if len(values) > 1 else {}
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("simulateTransaction options must be an object")
    accounts = options.get("accounts")
    if accounts is None:
        return None
    if not isinstance(accounts, dict):
        raise ValueError("simulateTransaction accounts option must be an object")
    addresses = accounts.get("addresses")
    if not isinstance(addresses, list):
        raise ValueError("simulateTransaction accounts.addresses must be an array")
    result: list[dict[str, Any] | None] = []
    for address in addresses:
        if not isinstance(address, str) or not address:
            raise ValueError(
                "simulateTransaction accounts.addresses entries must be strings"
            )
        record = _find_account(address, slot)
        result.append(_record_to_rpc_account(record) if record is not None else None)
    return result


def _set_current_slot(slot: int) -> None:
    global _CURRENT_SLOT
    _CURRENT_SLOT = slot


def _params_list(method: str, params: Any) -> list[Any]:
    if params is None:
        return []
    if not isinstance(params, list):
        raise ValueError(f"{method} params must be an array")
    return params


def _available_corpus_slots() -> list[int]:
    root = corpus_root()
    if not root.is_dir():
        return []
    slots: list[int] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        normalized = child.name.replace("_", "")
        if normalized.isdigit():
            slots.append(int(normalized))
    return sorted(slots)


def _current_slot() -> int:
    if _CURRENT_SLOT is not None:
        return _CURRENT_SLOT
    slots = _available_corpus_slots()
    return slots[-1] if slots else 0


def _load_slot_snapshot(slot: int) -> SlotSnapshot | None:
    cached = _SLOT_CACHE.get(slot)
    if cached is not None:
        return cached
    fixture = load_corpus_fixture(slot, "block")
    if fixture is None:
        return None
    snapshot = SlotSnapshot.from_raw(fixture)
    _SLOT_CACHE[slot] = snapshot
    _index_slot_transactions(snapshot)
    return snapshot


def _iter_known_slot_snapshots() -> list[SlotSnapshot]:
    snapshots: dict[int, SlotSnapshot] = dict(_SLOT_CACHE)
    for slot in _available_corpus_slots():
        snap = _load_slot_snapshot(slot)
        if snap is not None:
            snapshots[slot] = snap
    return [snapshots[slot] for slot in sorted(snapshots)]


def _blockhash_for_slot(slot: int) -> str:
    snapshot = _load_slot_snapshot(slot)
    if snapshot is not None and snapshot.blockhash:
        return snapshot.blockhash
    return _DEFAULT_BLOCKHASH


def _get_slot(_params: Any) -> int:
    return _current_slot()


def _get_latest_blockhash(_params: Any) -> dict[str, Any]:
    slot = _current_slot()
    return {
        "context": {"slot": slot},
        "value": {
            "blockhash": _blockhash_for_slot(slot),
            "lastValidBlockHeight": slot + 150,
        },
    }


def _get_recent_blockhash(_params: Any) -> dict[str, Any]:
    slot = _current_slot()
    return {
        "context": {"slot": slot},
        "value": {
            "blockhash": _blockhash_for_slot(slot),
            "feeCalculator": {"lamportsPerSignature": _LAMPORTS_PER_SIGNATURE},
        },
    }


def _extract_signatures(tx: dict[str, Any]) -> list[str]:
    inner = tx.get("transaction")
    if isinstance(inner, dict):
        signatures = inner.get("signatures")
    else:
        signatures = tx.get("signatures")
    return [sig for sig in signatures or [] if isinstance(sig, str)]


def _index_slot_transactions(snapshot: SlotSnapshot) -> None:
    for tx in snapshot.transactions:
        if not isinstance(tx, dict):
            continue
        for signature in _extract_signatures(tx):
            _TX_CACHE.setdefault(signature, (snapshot.slot, tx, snapshot))


def _tx_account_keys(tx: dict[str, Any]) -> list[str]:
    inner = tx.get("transaction")
    if not isinstance(inner, dict):
        inner = tx
    message = inner.get("message")
    if not isinstance(message, dict):
        return []
    keys = message.get("accountKeys") or message.get("staticAccountKeys") or []
    normalized: list[str] = []
    for key in keys:
        if isinstance(key, str):
            normalized.append(key)
        elif isinstance(key, dict) and isinstance(key.get("pubkey"), str):
            normalized.append(key["pubkey"])
    return normalized


def _tx_mentions_pubkey(tx: dict[str, Any], pubkey: str) -> bool:
    return pubkey in _tx_account_keys(tx)


def _find_transaction(
    signature: str,
) -> tuple[int, dict[str, Any], SlotSnapshot] | None:
    cached = _TX_CACHE.get(signature)
    if cached is not None:
        return cached
    for snapshot in _iter_known_slot_snapshots():
        _index_slot_transactions(snapshot)
    return _TX_CACHE.get(signature)


def _get_signatures_for_address(params: Any) -> list[dict[str, Any]]:
    values = _params_list("getSignaturesForAddress", params)
    if not values or not isinstance(values[0], str) or not values[0]:
        raise ValueError("getSignaturesForAddress params must be [pubkey, options?]")
    pubkey = values[0]
    limit = 1_000
    if len(values) > 1 and isinstance(values[1], dict) and values[1].get("limit"):
        limit = int(values[1]["limit"])

    signatures: list[dict[str, Any]] = []
    for snapshot in reversed(_iter_known_slot_snapshots()):
        for tx in snapshot.transactions:
            if not isinstance(tx, dict) or not _tx_mentions_pubkey(tx, pubkey):
                continue
            meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
            for signature in _extract_signatures(tx):
                signatures.append(
                    {
                        "signature": signature,
                        "slot": snapshot.slot,
                        "err": meta.get("err"),
                        "memo": None,
                        "blockTime": snapshot.block_time,
                        "confirmationStatus": "finalized",
                    }
                )
                if len(signatures) >= limit:
                    return signatures
    return signatures


def _get_transaction(params: Any) -> dict[str, Any] | None:
    values = _params_list("getTransaction", params)
    if not values or not isinstance(values[0], str) or not values[0]:
        raise ValueError("getTransaction params must be [signature, options?]")
    found = _find_transaction(values[0])
    if found is None:
        return None
    slot, tx, snapshot = found
    result = copy.deepcopy(tx)
    result.setdefault("slot", slot)
    result.setdefault("blockTime", snapshot.block_time)
    return result


def _program_ids_for_slot(slot: int) -> list[str]:
    slot_dir = corpus_root() / str(slot)
    if not slot_dir.is_dir():
        return []
    program_ids: set[str] = set()
    for path in slot_dir.glob("program_accounts-*.json*"):
        name = path.name
        if name.endswith(".json.gz"):
            program_ids.add(
                name.removeprefix("program_accounts-").removesuffix(".json.gz")
            )
        elif name.endswith(".json"):
            program_ids.add(
                name.removeprefix("program_accounts-").removesuffix(".json")
            )
    return sorted(program_ids)


def _load_account_snapshot(slot: int, program_id: str) -> AccountSnapshot | None:
    cache_key = (slot, program_id)
    cached = _ACCOUNT_SNAPSHOT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    fixture = load_corpus_fixture(slot, "program_accounts", program_id=program_id)
    if fixture is None:
        return None
    snapshot = AccountSnapshot.from_raw(fixture)
    _ACCOUNT_SNAPSHOT_CACHE[cache_key] = snapshot
    return snapshot


def _iter_account_records(slot: int) -> list[AccountRecord]:
    records: list[AccountRecord] = []
    for program_id in _program_ids_for_slot(slot):
        snapshot = _load_account_snapshot(slot, program_id)
        if snapshot is not None:
            records.extend(snapshot.accounts)
    return records


def _record_to_rpc_account(record: AccountRecord) -> dict[str, Any]:
    encoded = base64.b64encode(record.account_data).decode("ascii")
    return {
        "data": [encoded, "base64"],
        "executable": False,
        "lamports": record.lamports,
        "owner": record.owner,
        "rentEpoch": 0,
        "space": len(record.account_data),
    }


def _find_account(pubkey: str, slot: int) -> AccountRecord | None:
    for record in _iter_account_records(slot):
        if record.pubkey == pubkey:
            return record
    return None


def _get_account_info(params: Any) -> dict[str, Any]:
    values = _params_list("getAccountInfo", params)
    if not values or not isinstance(values[0], str) or not values[0]:
        raise ValueError("getAccountInfo params must be [pubkey, options?]")
    slot = _current_slot()
    record = _find_account(values[0], slot)
    return {
        "context": {"slot": slot},
        "value": _record_to_rpc_account(record) if record is not None else None,
    }


def _decode_base58(value: str) -> bytes:
    total = 0
    for char in value:
        total *= 58
        idx = _BASE58_ALPHABET.find(char)
        if idx < 0:
            raise ValueError("invalid base58 character")
        total += idx
    data = total.to_bytes((total.bit_length() + 7) // 8, "big") if total else b""
    pad = len(value) - len(value.lstrip("1"))
    return b"\x00" * pad + data


def _decode_memcmp_bytes(spec: dict[str, Any]) -> bytes:
    value = spec.get("bytes")
    if not isinstance(value, str):
        raise ValueError("memcmp filter bytes must be a string")
    encoding = str(spec.get("encoding") or "base58")
    if encoding == "base64":
        try:
            return base64.b64decode(value)
        except Exception as exc:
            raise ValueError("invalid base64 memcmp bytes") from exc
    if encoding == "bytes":
        return value.encode("utf-8")
    try:
        return _decode_base58(value)
    except ValueError:
        return value.encode("utf-8")


def _record_matches_filters(record: AccountRecord, filters: Any) -> bool:
    if filters is None:
        return True
    if not isinstance(filters, list):
        raise ValueError("getProgramAccounts filters must be an array")
    for item in filters:
        if not isinstance(item, dict):
            raise ValueError("getProgramAccounts filter entries must be objects")
        if "dataSize" in item:
            if len(record.account_data) != int(item["dataSize"]):
                return False
            continue
        memcmp = item.get("memcmp")
        if isinstance(memcmp, dict):
            offset = int(memcmp.get("offset", 0))
            expected = _decode_memcmp_bytes(memcmp)
            actual = record.account_data[offset : offset + len(expected)]
            if actual != expected:
                return False
            continue
        raise ValueError("unsupported getProgramAccounts filter")
    return True


def _get_program_accounts(params: Any) -> list[dict[str, Any]] | dict[str, Any]:
    values = _params_list("getProgramAccounts", params)
    if not values or not isinstance(values[0], str) or not values[0]:
        raise ValueError("getProgramAccounts params must be [program_id, options?]")
    program_id = values[0]
    options = values[1] if len(values) > 1 else {}
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError("getProgramAccounts options must be an object")

    slot = _current_slot()
    snapshot = _load_account_snapshot(slot, program_id)
    accounts: list[dict[str, Any]] = []
    if snapshot is not None:
        filters = options.get("filters")
        for record in snapshot.accounts:
            if _record_matches_filters(record, filters):
                accounts.append(
                    {
                        "pubkey": record.pubkey,
                        "account": _record_to_rpc_account(record),
                    }
                )
    if options.get("withContext") is True:
        return {"context": {"slot": slot}, "value": accounts}
    return accounts


_METHODS = {
    "simulateTransaction": _simulate_transaction,
    "getSlot": _get_slot,
    "getRecentBlockhash": _get_recent_blockhash,
    "getLatestBlockhash": _get_latest_blockhash,
    "getSignaturesForAddress": _get_signatures_for_address,
    "getTransaction": _get_transaction,
    "getAccountInfo": _get_account_info,
    "getProgramAccounts": _get_program_accounts,
}


def _is_write_or_signing_method(method: str) -> bool:
    return method in _WRITE_OR_SIGNING_METHODS or method.startswith("sign")


@router.post("")
def post_solana_rpc(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = payload.get("id")
    method = payload.get("method")
    if payload.get("jsonrpc") != _JSONRPC_VERSION or not isinstance(method, str):
        return _jsonrpc_error(
            request_id,
            -32600,
            "Invalid JSON-RPC request",
        )

    handler = _METHODS.get(method)
    if handler is None:
        if _is_write_or_signing_method(method):
            return _jsonrpc_error(
                request_id,
                -32601,
                f"Method not found: {method}. defi-sim is read-only and does not sign or send transactions.",
            )
        return _jsonrpc_error(
            request_id,
            -32601,
            f"Method not found: {method}",
        )

    try:
        result = handler(payload.get("params", []))
    except ValueError as exc:
        return _jsonrpc_error(request_id, -32602, str(exc))
    except HTTPException as exc:
        return _jsonrpc_error(request_id, -32000, str(exc.detail))

    return {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "result": result}
