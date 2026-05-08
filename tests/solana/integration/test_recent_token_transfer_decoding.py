"""Recent-RPC token transfer decoder coverage for FIX-003."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import pytest

from defi_sim_solana.program_ids import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from defi_sim_solana.replay.materialize import TokenTransferAction, materialize_slot
from defi_sim_solana.replay.slot_client import SlotSnapshot

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOLANA_RPC_URL"),
    reason="SOLANA_RPC_URL not set; recent-RPC token decoder lane disabled",
)

_JUP_MINT = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
_PYUSD_TOKEN_2022_MINT = "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo"


def _rpc_call(endpoint: str, method: str, params: list[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("error") is not None:
        raise RuntimeError(f"{method} RPC error: {body['error']!r}")
    return body.get("result")


def _recent_transfer_fixture(
    *,
    endpoint: str,
    address: str,
    program_id: str,
) -> dict[str, Any]:
    before: str | None = None
    for _ in range(3):
        config: dict[str, Any] = {"limit": 30, "commitment": "confirmed"}
        if before is not None:
            config["before"] = before
        signatures = _rpc_call(endpoint, "getSignaturesForAddress", [address, config])
        assert isinstance(signatures, list) and signatures, (
            f"recent signatures for {address} should be available"
        )
        scan_result = _scan_signatures_for_transfer(
            endpoint=endpoint,
            signatures=signatures,
            program_id=program_id,
        )
        if isinstance(scan_result, dict):
            return scan_result
        before = scan_result
        if before is None:
            break

    pytest.fail(
        f"no recent parsed transfer instruction found for {address} under {program_id}"
    )


def _scan_signatures_for_transfer(
    *,
    endpoint: str,
    signatures: list[Any],
    program_id: str,
) -> dict[str, Any] | str | None:
    next_before: str | None = None
    for entry in signatures:
        if not isinstance(entry, dict) or not isinstance(entry.get("signature"), str):
            continue
        next_before = entry["signature"]
        tx = _rpc_call(
            endpoint,
            "getTransaction",
            [
                next_before,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if not isinstance(tx, dict):
            continue
        message = ((tx.get("transaction") or {}).get("message") or {})
        instructions = message.get("instructions")
        if not isinstance(instructions, list):
            continue
        transfer_ix = _find_transfer_instruction(instructions, program_id)
        if transfer_ix is None:
            meta = tx.get("meta") or {}
            for group in meta.get("innerInstructions") or []:
                if not isinstance(group, dict):
                    continue
                inner = group.get("instructions")
                if isinstance(inner, list):
                    transfer_ix = _find_transfer_instruction(inner, program_id)
                    if transfer_ix is not None:
                        break
        if transfer_ix is not None:
            return {
                "slot": tx.get("slot") or entry.get("slot"),
                "transaction": {
                    "signatures": (tx.get("transaction") or {}).get(
                        "signatures",
                        [entry["signature"]],
                    ),
                    "message": {
                        "accountKeys": message.get("accountKeys") or [],
                        "instructions": [transfer_ix],
                    },
                },
                "meta": tx.get("meta") or {},
            }
    return next_before


def _find_transfer_instruction(
    instructions: list[Any],
    program_id: str,
) -> dict[str, Any] | None:
    for ix in instructions:
        if not isinstance(ix, dict) or ix.get("programId") != program_id:
            continue
        parsed = ix.get("parsed")
        ix_type = parsed.get("type") if isinstance(parsed, dict) else None
        if isinstance(ix_type, str) and "transfer" in ix_type.lower():
            return ix
    return None


@pytest.mark.parametrize(
    ("address", "program_id"),
    [
        (_JUP_MINT, TOKEN_PROGRAM),
        (_PYUSD_TOKEN_2022_MINT, TOKEN_2022_PROGRAM),
    ],
)
def test_recent_rpc_token_transfer_fixture_decodes_to_typed_action(
    address: str,
    program_id: str,
) -> None:
    endpoint = os.environ["SOLANA_RPC_URL"]
    tx = _recent_transfer_fixture(
        endpoint=endpoint,
        address=address,
        program_id=program_id,
    )
    snapshot = SlotSnapshot.from_raw({"slot": tx["slot"], "transactions": [tx]})

    [action] = materialize_slot(snapshot)

    assert isinstance(action, TokenTransferAction)
    assert action.token_program_id == program_id
    assert action.signature
    assert action.source
    assert action.destination
    assert action.amount > 0
    assert action.compute_unit_limit is not None
