"""One-off helper: hand-fill `expected.whirlpool` + `expected.tick_arrays`
for a freshly-captured corpus slot pointing at the canonical SOL/USDC
4 bps Whirlpool (Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE).

Usage::

    python tools/fill_whirlpool_manifest.py --slot 420196842

Parses the slot's `program_accounts-whirLb…json.gz` and
`program_accounts-Tokenkeg…json.gz`, finds the target pool + its tick
arrays + vault balances, and rewrites `manifest.yaml` in the same format
earlier Whirlpool manifests used.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import struct
import sys
from pathlib import Path

CORPUS_ROOT = Path("solana-plans/calibration/corpus")
TARGET_POOL = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Whirlpool layout offsets (after 8-byte Anchor discriminator).
WHIRLPOOL_DATA_SIZE = 653
TICK_SPACING_OFF = 41
FEE_RATE_OFF = 45
PROTOCOL_FEE_RATE_OFF = 47
LIQUIDITY_OFF = 49
SQRT_PRICE_OFF = 65
TICK_CURRENT_OFF = 81
MINT_A_OFF = 101
VAULT_A_OFF = 133
MINT_B_OFF = 181
VAULT_B_OFF = 213

# TickArray layout (Orca whirlpool): 9988 bytes total.
# 8 disc + 4 start_tick + 88 * 113 (ticks) + 32 (whirlpool field) = 9988.
TICK_ARRAY_DATA_SIZE = 9988
TICK_ARRAY_START_TICK_OFF = 8  # i32 after discriminator
TICKS_PER_ARRAY = 88
TICK_STRIDE = 113  # bytes per Tick struct
# Tick at +0 in each tick struct: initialized (u8)

# SPL Token account layout.
TOKEN_MINT_OFF = 0
TOKEN_OWNER_OFF = 32
TOKEN_AMOUNT_OFF = 64  # u64 LE

# base58
ALPHA = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = ALPHA[r] + out
    pad = sum(1 for x in b if x == 0)
    # Wait — leading-zero pad correctly:
    pad = 0
    for x in b:
        if x == 0:
            pad += 1
        else:
            break
    return "1" * pad + out


def load_program_accounts(slot: int, program_id: str) -> list[dict]:
    path = CORPUS_ROOT / str(slot) / f"program_accounts-{program_id}.json.gz"
    with gzip.open(path, "rb") as fh:
        payload = json.loads(fh.read())
    return payload["accounts"]


def find_pool(accounts: list[dict], target_pubkey: str) -> tuple[bytes, dict]:
    for a in accounts:
        if a["pubkey"] == target_pubkey:
            data = base64.b64decode(a["account"]["data"][0])
            return data, a
    raise RuntimeError(f"pool {target_pubkey} not in captured program_accounts")


def parse_pool(data: bytes) -> dict:
    ts = struct.unpack_from("<H", data, TICK_SPACING_OFF)[0]
    fee = struct.unpack_from("<H", data, FEE_RATE_OFF)[0]
    pfee = struct.unpack_from("<H", data, PROTOCOL_FEE_RATE_OFF)[0]
    liq_lo = struct.unpack_from("<Q", data, LIQUIDITY_OFF)[0]
    liq_hi = struct.unpack_from("<Q", data, LIQUIDITY_OFF + 8)[0]
    liquidity = liq_lo | (liq_hi << 64)
    sp_lo = struct.unpack_from("<Q", data, SQRT_PRICE_OFF)[0]
    sp_hi = struct.unpack_from("<Q", data, SQRT_PRICE_OFF + 8)[0]
    sqrt_price = sp_lo | (sp_hi << 64)
    tick_current = struct.unpack_from("<i", data, TICK_CURRENT_OFF)[0]
    return {
        "tick_spacing": ts,
        "fee_rate": fee,
        "protocol_fee_rate": pfee,
        "liquidity": liquidity,
        "sqrt_price_x64": sqrt_price,
        "tick_current_index": tick_current,
        "token_mint_a": b58encode(data[MINT_A_OFF : MINT_A_OFF + 32]),
        "token_vault_a": b58encode(data[VAULT_A_OFF : VAULT_A_OFF + 32]),
        "token_mint_b": b58encode(data[MINT_B_OFF : MINT_B_OFF + 32]),
        "token_vault_b": b58encode(data[VAULT_B_OFF : VAULT_B_OFF + 32]),
    }


def parse_tick_array(data: bytes) -> tuple[int, int]:
    start_tick = struct.unpack_from("<i", data, TICK_ARRAY_START_TICK_OFF)[0]
    initialized = 0
    for i in range(TICKS_PER_ARRAY):
        off = 12 + i * TICK_STRIDE  # 8 disc + 4 start + ticks…
        if data[off] != 0:
            initialized += 1
    return start_tick, initialized


def find_pool_tick_arrays(
    accounts: list[dict],
    pool_pubkey: str,
    tick_spacing: int,
    tick_current: int,
) -> list[dict]:
    """Find the 3 tick arrays that bracket the pool's current tick.

    Per Orca whirlpool: each tick array spans tick_spacing * 88 ticks.
    We pick the array containing tick_current and its two neighbors.
    """
    span = tick_spacing * TICKS_PER_ARRAY
    home_start = (tick_current // span) * span  # Python // is floor division.
    wanted = {home_start - span, home_start, home_start + span}
    out: dict[int, dict] = {}
    for a in accounts:
        raw = a["account"]["data"][0]
        if a["account"]["space"] != TICK_ARRAY_DATA_SIZE and len(raw) < 100:
            continue
        data = base64.b64decode(raw)
        if len(data) != TICK_ARRAY_DATA_SIZE:
            continue
        start_tick, init_count = parse_tick_array(data)
        if start_tick in wanted:
            out[start_tick] = {
                "pubkey": a["pubkey"],
                "start_tick_index": start_tick,
                "initialized_count": init_count,
            }
    return [out[k] for k in sorted(out)]


def token_amount(accounts: list[dict], vault_pubkey: str) -> int:
    for a in accounts:
        if a["pubkey"] == vault_pubkey:
            data = base64.b64decode(a["account"]["data"][0])
            return struct.unpack_from("<Q", data, TOKEN_AMOUNT_OFF)[0]
    raise RuntimeError(f"vault {vault_pubkey} not in captured token-program accounts")


def emit_manifest(slot: int, blockhash: str, block_height: int, parent_slot: int,
                  pool_pubkey: str, parsed: dict, vault_a_amt: int,
                  vault_b_amt: int, tick_arrays: list[dict]) -> str:
    lines = [
        f"# Captured by tools/fill_whirlpool_manifest.py at slot {slot}.",
        "# Real mainnet capture: canonical Orca SOL/USDC Whirlpool (4 bps tier)",
        "# with surrounding tick arrays + vault balances.",
        "",
        f"slot: {slot}",
        "category: high_volume_dex",
        f"block_height: {block_height}",
        f"parent_slot: {parent_slot}",
        f'blockhash: "{blockhash}"',
        "programs:",
        f'  - "{WHIRLPOOL_PROGRAM}"',
        f'  - "{TOKEN_PROGRAM}"',
        "expected:",
        "  whirlpool:",
        f'    pubkey: "{pool_pubkey}"',
        f'    tick_spacing: {parsed["tick_spacing"]}',
        f'    fee_rate: {parsed["fee_rate"]}',
        f'    protocol_fee_rate: {parsed["protocol_fee_rate"]}',
        f'    liquidity: {parsed["liquidity"]}',
        f'    sqrt_price_x64: {parsed["sqrt_price_x64"]}',
        f'    tick_current_index: {parsed["tick_current_index"]}',
        f'    token_mint_a: "{parsed["token_mint_a"]}"',
        f'    token_mint_b: "{parsed["token_mint_b"]}"',
        f'    token_vault_a: "{parsed["token_vault_a"]}"',
        f'    token_vault_b: "{parsed["token_vault_b"]}"',
        f"    vault_a_amount: {vault_a_amt}",
        f"    vault_b_amount: {vault_b_amt}",
        "  tick_arrays:",
    ]
    for ta in tick_arrays:
        lines.append(f'    - pubkey: "{ta["pubkey"]}"')
        lines.append(f'      start_tick_index: {ta["start_tick_index"]}')
        lines.append(f'      initialized_count: {ta["initialized_count"]}')
    lines.extend(["thresholds: {}", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--slot", type=int, required=True)
    p.add_argument("--pool", default=TARGET_POOL)
    args = p.parse_args(argv)

    slot = args.slot
    slot_dir = CORPUS_ROOT / str(slot)
    if not slot_dir.is_dir():
        print(f"ERR: {slot_dir} not found", file=sys.stderr)
        return 1

    wh_accounts = load_program_accounts(slot, WHIRLPOOL_PROGRAM)
    tk_accounts = load_program_accounts(slot, TOKEN_PROGRAM)

    pool_data, pool_record = find_pool(wh_accounts, args.pool)
    parsed = parse_pool(pool_data)
    tick_arrays = find_pool_tick_arrays(
        wh_accounts, args.pool, parsed["tick_spacing"], parsed["tick_current_index"]
    )
    vault_a_amt = token_amount(tk_accounts, parsed["token_vault_a"])
    vault_b_amt = token_amount(tk_accounts, parsed["token_vault_b"])

    # Pull block metadata from the manifest the snapshotter already wrote,
    # so we don't have to re-open the block.json.gz.
    block_path = slot_dir / "block.json.gz"
    with gzip.open(block_path, "rb") as fh:
        block = json.loads(fh.read())
    blockhash = block.get("blockhash", "")
    block_height = int(block.get("blockHeight") or 0)
    parent_slot = int(block.get("parentSlot") or 0)

    manifest = emit_manifest(
        slot=slot,
        blockhash=blockhash,
        block_height=block_height,
        parent_slot=parent_slot,
        pool_pubkey=args.pool,
        parsed=parsed,
        vault_a_amt=vault_a_amt,
        vault_b_amt=vault_b_amt,
        tick_arrays=tick_arrays,
    )
    (slot_dir / "manifest.yaml").write_text(manifest, encoding="utf-8")
    print(f"wrote {slot_dir / 'manifest.yaml'}")
    print(f"  tick_current={parsed['tick_current_index']}  "
          f"vault_a={vault_a_amt}  vault_b={vault_b_amt}  "
          f"tick_arrays={len(tick_arrays)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
