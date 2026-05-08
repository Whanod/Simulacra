"""Recent-RPC Whirlpool decoder coverage for FIX-004."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from hashlib import sha256
from typing import Any

import pytest

from defi_sim_solana.program_ids import WHIRLPOOL_PROGRAM
from defi_sim_solana.replay.materialize import (
    MaterializedSwapAction,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLANA_RPC_URL"),
    reason="SOLANA_RPC_URL not set; recent-RPC Whirlpool decoder lane disabled",
)

_WHIRLPOOL_SWAP_DISCRIMINATORS = frozenset(
    (
        sha256(b"global:swap").digest()[:8],
        bytes((43, 4, 237, 11, 26, 201, 30, 98)),
    )
)
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: idx for idx, ch in enumerate(_BASE58_ALPHABET)}


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


def _recent_whirlpool_swap_fixture(endpoint: str) -> dict[str, Any]:
    before: str | None = None
    for _ in range(4):
        config: dict[str, Any] = {"limit": 50, "commitment": "confirmed"}
        if before is not None:
            config["before"] = before
        signatures = _rpc_call(
            endpoint,
            "getSignaturesForAddress",
            [WHIRLPOOL_PROGRAM, config],
        )
        assert isinstance(signatures, list) and signatures, (
            "recent Whirlpool program signatures should be available"
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

    pytest.fail("no recent direct Whirlpool swap instruction found")


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
        minimized = _minimize_to_direct_whirlpool_swap(tx)
        if minimized is not None:
            return minimized
    return next_before


def _minimize_to_direct_whirlpool_swap(tx: dict[str, Any]) -> dict[str, Any] | None:
    message = ((tx.get("transaction") or {}).get("message") or {})
    account_keys = _full_account_keys(message, tx.get("meta") or {})
    instructions = message.get("instructions")
    if not isinstance(instructions, list):
        return None
    for instruction_index, ix in enumerate(instructions):
        if not isinstance(ix, dict):
            continue
        if _program_id(ix, account_keys) != WHIRLPOOL_PROGRAM:
            continue
        if _instruction_data(ix)[:8] not in _WHIRLPOOL_SWAP_DISCRIMINATORS:
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


def test_recent_rpc_whirlpool_swap_fixture_decodes_to_typed_action() -> None:
    endpoint = os.environ["SOLANA_RPC_URL"]
    tx = _recent_whirlpool_swap_fixture(endpoint)
    snapshot = SlotSnapshot.from_raw({"slot": tx["slot"], "transactions": [tx]})

    [action] = materialize_slot(snapshot)

    assert isinstance(action, MaterializedSwapAction)
    assert action.protocol_program_id == WHIRLPOOL_PROGRAM
    assert action.signature
    assert action.pool_id
    assert action.source_token_account
    assert action.destination_token_account
    assert action.amount_in > 0
    assert action.compute_unit_limit is not None
