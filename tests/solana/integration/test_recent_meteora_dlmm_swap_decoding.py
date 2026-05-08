"""Recent-RPC Meteora DLMM decoder coverage for FIX-009."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

import pytest

from defi_sim_solana.program_ids import METEORA_DLMM_PROGRAM
from defi_sim_solana.replay.materialize import (
    MaterializedSwapAction,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLANA_RPC_URL"),
    reason="SOLANA_RPC_URL not set; recent-RPC DLMM decoder lane disabled",
)

_DLMM_SWAP_DISCRIMINATORS = frozenset(
    (
        bytes((248, 198, 158, 145, 225, 117, 135, 200)),
        bytes((65, 75, 63, 76, 235, 91, 91, 136)),
        bytes((250, 73, 101, 33, 38, 207, 75, 184)),
        bytes((43, 215, 247, 132, 137, 60, 243, 81)),
        bytes((56, 173, 230, 208, 173, 228, 156, 205)),
        bytes((74, 98, 192, 214, 177, 51, 75, 51)),
    )
)
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: idx for idx, ch in enumerate(_BASE58_ALPHABET)}
_KNOWN_RECENT_DLMM_SWAP_SIGNATURES = (
    "nwYUKPE12qfjfHntznGY7bd3AfoxRszqoo5XzXVE1T8SctFMEgFUtMLdbVuL7tSdNhmAcpBf9dHPi5b76J27BSW",
)


def _rpc_call(endpoint: str, method: str, params: list[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise RuntimeError(f"{method} RPC error: {body['error']!r}")
    return body.get("result")


def _recent_dlmm_swap_fixture(endpoint: str) -> dict[str, Any]:
    for signature in _KNOWN_RECENT_DLMM_SWAP_SIGNATURES:
        try:
            tx = _rpc_call(
                endpoint,
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "json",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
        except (TimeoutError, socket.timeout, urllib.error.URLError, RuntimeError):
            continue
        if isinstance(tx, dict):
            minimized = _minimize_to_direct_dlmm_swap(tx)
            if minimized is not None:
                return minimized

    before: str | None = None
    for _ in range(8):
        config: dict[str, Any] = {"limit": 50, "commitment": "confirmed"}
        if before is not None:
            config["before"] = before
        signatures = _rpc_call(
            endpoint,
            "getSignaturesForAddress",
            [METEORA_DLMM_PROGRAM, config],
        )
        assert isinstance(signatures, list) and signatures, (
            "recent Meteora DLMM program signatures should be available"
        )
        result = _scan_signatures_for_direct_swap(
            endpoint=endpoint,
            signatures=signatures,
        )
        if isinstance(result, dict):
            return result
        before = result
        if before is None:
            break

    pytest.fail("no recent direct Meteora DLMM swap instruction found")


def _scan_signatures_for_direct_swap(
    *,
    endpoint: str,
    signatures: list[Any],
) -> dict[str, Any] | str | None:
    next_before: str | None = None
    for entry in signatures:
        if not isinstance(entry, dict) or not isinstance(entry.get("signature"), str):
            continue
        next_before = entry["signature"]
        if entry.get("err") is not None:
            continue
        try:
            tx = _rpc_call(
                endpoint,
                "getTransaction",
                [
                    next_before,
                    {
                        "encoding": "json",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            )
        except (TimeoutError, socket.timeout, urllib.error.URLError, RuntimeError):
            continue
        if not isinstance(tx, dict):
            continue
        minimized = _minimize_to_direct_dlmm_swap(tx)
        if minimized is not None:
            return minimized
    return next_before


def _minimize_to_direct_dlmm_swap(tx: dict[str, Any]) -> dict[str, Any] | None:
    message = (tx.get("transaction") or {}).get("message") or {}
    account_keys = _full_account_keys(message, tx.get("meta") or {})
    instructions = message.get("instructions")
    if not isinstance(instructions, list):
        return None
    for instruction_index, ix in enumerate(instructions):
        if not isinstance(ix, dict):
            continue
        if _program_id(ix, account_keys) != METEORA_DLMM_PROGRAM:
            continue
        if _instruction_data(ix)[:8] not in _DLMM_SWAP_DISCRIMINATORS:
            continue
        meta = tx.get("meta") if isinstance(tx.get("meta"), dict) else {}
        return {
            "slot": tx.get("slot"),
            "transaction": {
                "signatures": (tx.get("transaction") or {}).get("signatures", []),
                "message": {
                    "accountKeys": message.get("accountKeys") or [],
                    "instructions": [ix],
                },
            },
            "meta": {
                "computeUnitsConsumed": meta.get("computeUnitsConsumed") or 0,
                "loadedAddresses": meta.get("loadedAddresses") or {},
                "preTokenBalances": meta.get("preTokenBalances") or [],
                "postTokenBalances": meta.get("postTokenBalances") or [],
                "innerInstructions": _inner_group_for(meta, instruction_index),
            },
        }
    return None


def _full_account_keys(message: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in message.get("accountKeys") or []:
        if isinstance(key, str):
            out.append(key)
        elif isinstance(key, dict) and isinstance(key.get("pubkey"), str):
            out.append(key["pubkey"])
    loaded = meta.get("loadedAddresses")
    if isinstance(loaded, dict):
        for group in ("writable", "readonly"):
            out.extend(k for k in loaded.get(group) or [] if isinstance(k, str))
    return out


def _program_id(ix: dict[str, Any], account_keys: list[str]) -> str | None:
    direct = ix.get("programId")
    if isinstance(direct, str):
        return direct
    index = ix.get("programIdIndex")
    if isinstance(index, int) and 0 <= index < len(account_keys):
        return account_keys[index]
    return None


def _instruction_data(ix: dict[str, Any]) -> bytes:
    data = ix.get("data")
    if isinstance(data, str):
        return _decode_base58(data)
    return b""


def _decode_base58(value: str) -> bytes:
    if value == "":
        return b""
    num = 0
    for char in value:
        digit = _BASE58_INDEX.get(char)
        if digit is None:
            return b""
        num = num * 58 + digit
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def _inner_group_for(meta: dict[str, Any], original_index: int) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for group in meta.get("innerInstructions") or []:
        if not isinstance(group, dict) or group.get("index") != original_index:
            continue
        inner = group.get("instructions")
        if isinstance(inner, list):
            groups.append({"index": 0, "instructions": inner})
    return groups


def test_recent_rpc_dlmm_swap_fixture_decodes_to_typed_action() -> None:
    endpoint = os.environ["SOLANA_RPC_URL"]
    tx = _recent_dlmm_swap_fixture(endpoint)
    snapshot = SlotSnapshot.from_raw({"slot": tx["slot"], "transactions": [tx]})

    [action] = materialize_slot(snapshot)

    assert isinstance(action, MaterializedSwapAction)
    assert action.protocol_program_id == METEORA_DLMM_PROGRAM
    assert action.signature
    assert action.pool_id
    assert action.source_token_account
    assert action.destination_token_account
    assert action.pool_reserve_accounts
    assert action.amount_in > 0
    assert action.compute_unit_limit is not None
