"""Whirlpool / TickArray / SPL-vault parser (PRD US-003 / 2.3b reference impl).

Parses Orca Whirlpool program account bytes plus the SPL-token vault
accounts that back the pool into typed
:class:`~defi_sim.engine.initial_state.InitialStateFragment` records.

Three fragment shapes:

* ``payload["subkind"] == "pool"`` — the Whirlpool pool struct
  (sqrt_price_x64, liquidity, tick_current_index, fee tiers, vault pubkeys,
  mint pubkeys). Carried under the canonical ``kind="pool"`` so existing
  ``materialize_fork`` routing reaches it.
* ``payload["subkind"] == "pool_tick_array"`` — a parsed TickArray
  account, also under ``kind="pool"`` so it co-routes with the pool
  fragment to :class:`WhirlpoolMarket.from_initial_state`.
* ``payload["subkind"] == "vault_balance"`` — an SPL-token vault account
  carrying the live ``amount`` (uint64). Routed under ``kind="pool"`` so
  Whirlpool materialization can pick it up alongside the pool/tick fragments.

The hydrator's ``parse_account`` only handles the Whirlpool pool struct —
tick arrays and vaults are surfaced via dedicated helpers
(:func:`parse_tick_array_account` and :func:`parse_token_vault_account`)
so the loader can dispatch on byte-discriminator without forcing the
``StateHydrator`` ABC to grow per-account-shape methods.

Account layout (Orca Whirlpool ``Whirlpool`` struct, little-endian)::

    0    discriminator              [u8; 8]            3f 95 d1 0c e1 80 63 09
    8    whirlpools_config          Pubkey (32)
    40   whirlpool_bump             u8
    41   tick_spacing               u16
    43   tick_spacing_seed          [u8; 2]
    45   fee_rate                   u16
    47   protocol_fee_rate          u16
    49   liquidity                  u128
    65   sqrt_price                 u128
    81   tick_current_index         i32
    85   protocol_fee_owed_a        u64
    93   protocol_fee_owed_b        u64
    101  token_mint_a               Pubkey
    133  token_vault_a              Pubkey
    165  fee_growth_global_a        u128
    181  token_mint_b               Pubkey
    213  token_vault_b              Pubkey
    245  fee_growth_global_b        u128
    261  reward_last_updated_timestamp  u64
    269  reward_infos[3] (3 × 128 bytes)              -> 269..653

TickArray (FixedTickArray) layout, little-endian::

    0    discriminator               [u8; 8]    45 61 bd be 6e 07 42 bb
    8    start_tick_index            i32
    12   ticks[88] (each 113 bytes)              12..(12 + 88*113 = 9956)
    9956 whirlpool                   Pubkey (32)

Each Tick (113 bytes)::

    0    initialized                 bool (u8)
    1    liquidity_net               i128
    17   liquidity_gross             u128
    33   fee_growth_outside_a        u128
    49   fee_growth_outside_b        u128
    65   reward_growths_outside[3]   u128 × 3 (= 48)

SPL-token Account (165 bytes, little-endian, classic SPL Token program)::

    0    mint                        Pubkey (32)
    32   owner                       Pubkey (32)
    64   amount                      u64
    72   delegate (option/Pubkey)    36
    108  state                       u8
    109  is_native (option/u64)      12
    121  delegated_amount            u64
    129  close_authority (option)    36
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import (
    AccountFilter,
    OracleId,
    StateHydrator,
)

__all__ = [
    "WHIRLPOOL_POOL_DISCRIMINATOR",
    "WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR",
    "WHIRLPOOL_PROGRAM",
    "WhirlpoolPoolFragment",
    "WhirlpoolStateHydrator",
    "parse_tick_array_account",
    "parse_token_vault_account",
]

WHIRLPOOL_PROGRAM = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
# sha256("account:Whirlpool")[:8]
WHIRLPOOL_POOL_DISCRIMINATOR = b"\x3f\x95\xd1\x0c\xe1\x80\x63\x09"
# sha256("account:TickArray")[:8] — also the discriminator FixedTickArray uses.
WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR = b"\x45\x61\xbd\xbe\x6e\x07\x42\xbb"

_MIN_POOL_LEN_BASIC = 85    # discriminator + config..tick_current_index
_MIN_POOL_LEN_EXTENDED = 261  # adds vault/mint/fee-growth fields
_TICK_ARRAY_LEN = 9988  # 8 + 4 + 88*113 + 32
_TICK_LEN = 113
_TICK_ARRAY_PAYLOAD_OFFSET = 12

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _BASE58_ALPHABET[r] + s
    pad = 0
    for byte in b:
        if byte == 0:
            pad += 1
        else:
            break
    return _BASE58_ALPHABET[0] * pad + s


@dataclass(frozen=True, slots=True)
class WhirlpoolPoolFragment:
    """Typed view of the parsed Whirlpool pool fields.

    The first four fields (``pubkey``, ``liquidity``, ``sqrt_price_x64``,
    ``tick_current_index``) are the original 2.3-reference reserve-proxy
    surface and are populated for both 85-byte synthetic fixtures and
    261-byte real captures. The remaining swap-relevant fields populate
    only when the fixture is the full Whirlpool struct (real captures).
    """

    pubkey: str
    liquidity: int
    sqrt_price_x64: int
    tick_current_index: int
    tick_spacing: int = 0
    fee_rate: int = 0
    protocol_fee_rate: int = 0
    fee_growth_global_a: int = 0
    fee_growth_global_b: int = 0
    protocol_fee_owed_a: int = 0
    protocol_fee_owed_b: int = 0
    token_mint_a: str = ""
    token_mint_b: str = ""
    token_vault_a: str = ""
    token_vault_b: str = ""

    @property
    def reserve_proxy(self) -> tuple[int, int]:
        return (self.liquidity, self.sqrt_price_x64)


def parse_tick_array_account(pubkey: str, data: bytes) -> dict[str, Any]:
    """Parse one Whirlpool ``TickArray`` account into a payload dict.

    Validates the discriminator and length; returns a dict matching the
    ``payload`` shape expected by
    :meth:`defi_sim.markets.whirlpool.WhirlpoolMarket.from_initial_state`.
    """
    if len(data) < _TICK_ARRAY_LEN:
        raise ValueError(
            f"TickArray account {pubkey!r} is {len(data)} bytes; need {_TICK_ARRAY_LEN}"
        )
    if not data[:8] == WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR:
        raise ValueError(
            f"TickArray account {pubkey!r} discriminator mismatch (got {data[:8].hex()})"
        )
    start_tick_index = int.from_bytes(data[8:12], "little", signed=True)
    ticks: list[dict[str, Any]] = []
    for i in range(88):
        base = _TICK_ARRAY_PAYLOAD_OFFSET + i * _TICK_LEN
        initialized = bool(data[base])
        liquidity_net = int.from_bytes(data[base + 1 : base + 17], "little", signed=True)
        liquidity_gross = int.from_bytes(data[base + 17 : base + 33], "little")
        fee_growth_outside_a = int.from_bytes(data[base + 33 : base + 49], "little")
        fee_growth_outside_b = int.from_bytes(data[base + 49 : base + 65], "little")
        ticks.append(
            {
                "initialized": initialized,
                "liquidity_net": liquidity_net,
                "liquidity_gross": liquidity_gross,
                "fee_growth_outside_a": fee_growth_outside_a,
                "fee_growth_outside_b": fee_growth_outside_b,
            }
        )
    whirlpool = _b58encode(data[9956:9988])
    return {
        "subkind": "pool_tick_array",
        "start_tick_index": start_tick_index,
        "ticks": ticks,
        "whirlpool": whirlpool,
    }


def parse_token_vault_account(pubkey: str, data: bytes) -> dict[str, Any]:
    """Parse an SPL-token vault account into a vault-balance fragment payload."""
    if len(data) < 72:
        raise ValueError(
            f"SPL vault account {pubkey!r} is {len(data)} bytes; need at least 72"
        )
    mint = _b58encode(data[0:32])
    owner = _b58encode(data[32:64])
    amount = int.from_bytes(data[64:72], "little")
    return {
        "subkind": "vault_balance",
        "pubkey": pubkey,
        "mint": mint,
        "owner": owner,
        "amount": amount,
    }


class WhirlpoolStateHydrator(StateHydrator):
    """Whirlpool pool-account parser (PRD line 216 / 2.3b reference impl)."""

    program_id: str = WHIRLPOOL_PROGRAM
    schema_version: int = 2

    def account_filters(self) -> list[AccountFilter]:
        return [
            AccountFilter(discriminator=WHIRLPOOL_POOL_DISCRIMINATOR),
            AccountFilter(discriminator=WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR),
        ]

    def oracle_dependencies(self) -> list[OracleId]:
        return []

    def parse_pool(self, pubkey: str, data: bytes) -> WhirlpoolPoolFragment:
        if len(data) < _MIN_POOL_LEN_BASIC:
            raise ValueError(
                f"Whirlpool pool account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_MIN_POOL_LEN_BASIC} to read "
                "liquidity + sqrt_price + tick."
            )
        liquidity = int.from_bytes(data[49:65], "little")
        sqrt_price_x64 = int.from_bytes(data[65:81], "little")
        tick_current_index = int.from_bytes(data[81:85], "little", signed=True)
        if len(data) < _MIN_POOL_LEN_EXTENDED:
            # Legacy synthetic fixture (85-byte hand-crafted struct used by
            # the 2.3-reference reserve-proxy validation). The extended
            # fields stay at their dataclass defaults so existing tests
            # that only assert on the reserve proxy keep working.
            return WhirlpoolPoolFragment(
                pubkey=pubkey,
                liquidity=liquidity,
                sqrt_price_x64=sqrt_price_x64,
                tick_current_index=tick_current_index,
            )
        return WhirlpoolPoolFragment(
            pubkey=pubkey,
            liquidity=liquidity,
            sqrt_price_x64=sqrt_price_x64,
            tick_current_index=tick_current_index,
            tick_spacing=struct.unpack_from("<H", data, 41)[0],
            fee_rate=struct.unpack_from("<H", data, 45)[0],
            protocol_fee_rate=struct.unpack_from("<H", data, 47)[0],
            protocol_fee_owed_a=int.from_bytes(data[85:93], "little"),
            protocol_fee_owed_b=int.from_bytes(data[93:101], "little"),
            token_mint_a=_b58encode(data[101:133]),
            token_vault_a=_b58encode(data[133:165]),
            fee_growth_global_a=int.from_bytes(data[165:181], "little"),
            token_mint_b=_b58encode(data[181:213]),
            token_vault_b=_b58encode(data[213:245]),
            fee_growth_global_b=int.from_bytes(data[245:261], "little"),
        )

    def parse_account(self, pubkey: str, data: bytes) -> InitialStateFragment:
        if data[:8] == WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR:
            payload = parse_tick_array_account(pubkey, data)
            return InitialStateFragment(
                kind="pool",
                protocol_model="Whirlpool",
                pubkey=pubkey,
                owner=None,
                payload=payload,
            )
        pool = self.parse_pool(pubkey, data)
        return InitialStateFragment(
            kind="pool",
            protocol_model="Whirlpool",
            pubkey=pubkey,
            owner=None,
            payload={
                "subkind": "pool",
                "pubkey": pool.pubkey,
                "tick_spacing": pool.tick_spacing,
                "fee_rate": pool.fee_rate,
                "protocol_fee_rate": pool.protocol_fee_rate,
                "liquidity": pool.liquidity,
                "sqrt_price_x64": pool.sqrt_price_x64,
                "tick_current_index": pool.tick_current_index,
                "fee_growth_global_a": pool.fee_growth_global_a,
                "fee_growth_global_b": pool.fee_growth_global_b,
                "protocol_fee_owed_a": pool.protocol_fee_owed_a,
                "protocol_fee_owed_b": pool.protocol_fee_owed_b,
                "token_mint_a": pool.token_mint_a,
                "token_mint_b": pool.token_mint_b,
                "token_vault_a": pool.token_vault_a,
                "token_vault_b": pool.token_vault_b,
                "reserve_proxy": list(pool.reserve_proxy),
            },
        )
