"""Targeted Whirlpool corpus snapshot.

Captures the minimal account set the replay engine + hydrator actually need
for a single pool at a single slot:

  * 1 Whirlpool pool account
  * 3 surrounding TickArray accounts (home + neighbors of tick_current)
  * 2 token vault accounts

Writes them in the same JSON+gzip format that `cache_corpus_slot.py`
produces, so the existing corpus loader (`load_corpus_fixture`) picks them
up unchanged. Then runs `tools/fill_whirlpool_manifest.py` to hand-fill the
manifest.yaml.

Why not `cache_corpus_slot.py`? That tool does an unfiltered
`getProgramAccounts` on the Whirlpool program (thousands of pools + tens of
thousands of tick arrays + positions). Most public RPCs time out on it.
Earlier captures were post-processed down to 4 accounts anyway, so we go
targeted from the start.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import struct
import sys
import urllib.request
from pathlib import Path

CORPUS_ROOT = Path("solana-plans/calibration/corpus")
WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TICKS_PER_ARRAY = 88
TICK_ARRAY_DATA_SIZE = 9988  # 8 + 4 + 88*113 + 32

# Whirlpool pool layout
TICK_SPACING_OFF = 41
TICK_CURRENT_OFF = 81
MINT_A_OFF = 101
VAULT_A_OFF = 133
MINT_B_OFF = 181
VAULT_B_OFF = 213

ALPHA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = ALPHA[r] + out
    pad = 0
    for x in b:
        if x == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def rpc(method, params, *, timeout=60):
    endpoint = os.environ["SOLANA_RPC_URL"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_block(slot: int) -> dict:
    r = rpc("getBlock", [slot, {
        "encoding": "json",
        "transactionDetails": "none",
        "rewards": False,
        "commitment": "confirmed",
        "maxSupportedTransactionVersion": 0,
    }])
    if "error" in r:
        raise RuntimeError(f"getBlock({slot}) failed: {r['error']}")
    block = r["result"]
    # getBlock's result has parentSlot but not its own slot. Loaders
    # (load_corpus_fixture, SlotSnapshot.from_block_payload) key off
    # ``slot`` so we add it explicitly.
    block["slot"] = slot
    return block


def get_account(pubkey: str) -> dict:
    r = rpc("getAccountInfo", [pubkey, {"encoding": "base64", "commitment": "confirmed"}])
    if "error" in r or r["result"]["value"] is None:
        raise RuntimeError(f"getAccountInfo({pubkey}) failed: {r}")
    val = r["result"]["value"]
    return {
        "pubkey": pubkey,
        "account": {
            "data": val["data"],
            "executable": val["executable"],
            "lamports": val["lamports"],
            "owner": val["owner"],
            "rentEpoch": val["rentEpoch"],
            "space": val.get("space", len(base64.b64decode(val["data"][0]))),
        },
    }


def get_pool_tick_arrays(pool_pubkey: str) -> list[dict]:
    """Fetch every TickArray owned by `pool_pubkey` via memcmp filter."""
    r = rpc("getProgramAccounts", [WHIRLPOOL_PROGRAM, {
        "encoding": "base64",
        "commitment": "confirmed",
        "filters": [
            {"dataSize": TICK_ARRAY_DATA_SIZE},
            {"memcmp": {"offset": 9956, "bytes": pool_pubkey}},
        ],
    }], timeout=120)
    if "error" in r:
        raise RuntimeError(f"getProgramAccounts tick arrays failed: {r['error']}")
    return r["result"]


def pick_neighbor_arrays(arrays: list[dict], tick_current: int, tick_spacing: int) -> list[dict]:
    span = tick_spacing * TICKS_PER_ARRAY
    home = (tick_current // span) * span  # Python's // is floor division — handles negatives.
    wanted = {home - span, home, home + span}
    keyed: dict[int, dict] = {}
    for a in arrays:
        data = base64.b64decode(a["account"]["data"][0])
        start = struct.unpack_from("<i", data, 8)[0]
        if start in wanted:
            keyed[start] = a
    return [keyed[k] for k in sorted(keyed) if k in keyed]


def write_gz(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(blob, mtime=0))


def snapshot(slot: int, pool_pubkey: str) -> None:
    slot_dir = CORPUS_ROOT / str(slot)
    slot_dir.mkdir(parents=True, exist_ok=True)

    print(f"[{slot}] getBlock…", flush=True)
    block = get_block(slot)
    write_gz(slot_dir / "block.json.gz", block)

    print(f"[{slot}] pool account…", flush=True)
    pool = get_account(pool_pubkey)
    pool_data = base64.b64decode(pool["account"]["data"][0])
    tick_spacing = struct.unpack_from("<H", pool_data, TICK_SPACING_OFF)[0]
    tick_current = struct.unpack_from("<i", pool_data, TICK_CURRENT_OFF)[0]
    vault_a = b58encode(pool_data[VAULT_A_OFF:VAULT_A_OFF + 32])
    vault_b = b58encode(pool_data[VAULT_B_OFF:VAULT_B_OFF + 32])
    print(f"[{slot}]   tick_current={tick_current}  tick_spacing={tick_spacing}")

    print(f"[{slot}] tick arrays for pool (memcmp@9956)…", flush=True)
    all_arrays = get_pool_tick_arrays(pool_pubkey)
    print(f"[{slot}]   {len(all_arrays)} total tick arrays owned by pool")
    neighbors = pick_neighbor_arrays(all_arrays, tick_current, tick_spacing)
    print(f"[{slot}]   {len(neighbors)} neighbor tick arrays selected")

    whirlpool_accounts = [pool, *neighbors]
    write_gz(
        slot_dir / f"program_accounts-{WHIRLPOOL_PROGRAM}.json.gz",
        {"program_id": WHIRLPOOL_PROGRAM, "slot": slot, "accounts": whirlpool_accounts},
    )

    print(f"[{slot}] vault accounts…", flush=True)
    vault_a_acc = get_account(vault_a)
    vault_b_acc = get_account(vault_b)
    write_gz(
        slot_dir / f"program_accounts-{TOKEN_PROGRAM}.json.gz",
        {"program_id": TOKEN_PROGRAM, "slot": slot, "accounts": [vault_a_acc, vault_b_acc]},
    )

    print(f"[{slot}] done.")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--slot", type=int, required=True)
    p.add_argument("--pool", default="Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE")
    args = p.parse_args(argv)
    snapshot(args.slot, args.pool)
    return 0


if __name__ == "__main__":
    sys.exit(main())
