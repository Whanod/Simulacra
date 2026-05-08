"""Build :class:`WhirlpoolMarket` from a captured corpus slot.

Loads ``program_accounts-<whirlpool_program>.json[.gz]`` plus
``program_accounts-<spl_token_program>.json[.gz]`` from
``solana-plans/calibration/corpus/<slot>/``, parses them via
:class:`defi_sim_solana.replay.whirlpool_hydrator.WhirlpoolStateHydrator`
and the SPL-vault helper, and returns a runtime
:class:`defi_sim.markets.whirlpool.WhirlpoolMarket`.

Decimals for the two tokens are taken from the SPL-mint accounts when
available; if the corpus fixture omits the mint accounts, callers must
supply ``token_a_decimals`` / ``token_b_decimals`` (the public mints SOL=9
and USDC=6 are the default behaviour).
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import Any

from defi_sim.core.types import Token
from defi_sim.markets.whirlpool import (
    TickArrayState,
    TickEntry,
    WhirlpoolMarket,
    WhirlpoolPoolState,
)
from defi_sim_solana.replay.corpus import corpus_root
from defi_sim_solana.replay.whirlpool_hydrator import (
    WHIRLPOOL_POOL_DISCRIMINATOR,
    WHIRLPOOL_PROGRAM,
    WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR,
    WhirlpoolStateHydrator,
    parse_token_vault_account,
)

__all__ = [
    "build_whirlpool_market_from_corpus",
    "WHIRLPOOL_PROGRAM",
]

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

_WELL_KNOWN_DECIMALS: dict[str, tuple[str, int]] = {
    # mint -> (symbol, decimals)
    "So11111111111111111111111111111111111111112": ("SOL", 9),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6),
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": ("mSOL", 9),
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": ("JitoSOL", 9),
}


def _read_program_fixture(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix == ".gz":
        return json.loads(gzip.decompress(path.read_bytes()))
    return json.loads(path.read_bytes())


def _decode_account_data(data: Any) -> bytes:
    if isinstance(data, list) and len(data) >= 2:
        if data[1] == "base64":
            return base64.b64decode(data[0])
        if data[1] == "base58":
            from defi_sim_solana.replay.account_client import _decode_account_data as fallback

            return fallback(data)
    if isinstance(data, str):
        return base64.b64decode(data)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return b""


def _resolve_token(
    mint: str,
    *,
    token_id_override: str,
    token_symbol_override: str,
) -> Token:
    if mint in _WELL_KNOWN_DECIMALS:
        symbol, decimals = _WELL_KNOWN_DECIMALS[mint]
    else:
        symbol = mint[:6]
        decimals = 6
    if token_symbol_override:
        symbol = token_symbol_override
    # Prefer ``token_symbol_override`` as the canonical engine-side token
    # id. The Builder's corpus dropdown is keyed by mint pubkey and snaps
    # ``token_a_id`` / ``token_b_id`` to those mints (see
    # ``frontend/src/app/(studio)/builder/page.tsx:536``); if we used a
    # mint pubkey as the engine id, agent ``initial_balances`` (keyed by
    # symbol via the template's tokens list) would never resolve, every
    # swap action would silently fail with a balance lookup miss, and
    # cumulative volume / fees / range metrics would all stay at zero.
    # Falls back to the explicit id override or well-known symbol when no
    # symbol override is provided.
    token_id = token_symbol_override or token_id_override or symbol
    return Token(id=token_id, symbol=symbol, decimals=decimals)


def _scale_signed(x: int, num: int, den: int) -> int:
    """Round-toward-zero integer scaling of ``x * num / den``."""
    if den == 0 or num == den:
        return x
    if x >= 0:
        return (x * num) // den
    return -((-x * num) // den)


def build_whirlpool_market_from_corpus(
    *,
    corpus_slot: int,
    pool_pubkey: str,
    token_a_id: str = "",
    token_b_id: str = "",
    token_a_symbol: str = "",
    token_b_symbol: str = "",
    fee_model: Any = None,
    pool_account_id: str | None = None,
    initial_liquidity: int | float | None = None,
    fee_rate_override: int | None = None,
) -> WhirlpoolMarket:
    root = corpus_root() / str(corpus_slot)
    if not root.exists():
        raise FileNotFoundError(f"corpus slot directory does not exist: {root}")

    whirl_path_gz = root / f"program_accounts-{WHIRLPOOL_PROGRAM}.json.gz"
    whirl_path = root / f"program_accounts-{WHIRLPOOL_PROGRAM}.json"
    fixture = _read_program_fixture(whirl_path_gz if whirl_path_gz.exists() else whirl_path)

    hydrator = WhirlpoolStateHydrator()
    pool_state: WhirlpoolPoolState | None = None
    tick_arrays: list[TickArrayState] = []
    pool_fragment: Any = None
    pool_payload: dict[str, Any] | None = None
    for entry in fixture.get("accounts", []):
        pubkey = str(entry.get("pubkey", ""))
        data = _decode_account_data(entry.get("account", {}).get("data"))
        if not data:
            continue
        if data[:8] == WHIRLPOOL_POOL_DISCRIMINATOR and pubkey == pool_pubkey:
            pool_fragment = hydrator.parse_account(pubkey, data)
            pool_payload = dict(pool_fragment.payload)
        elif data[:8] == WHIRLPOOL_TICK_ARRAY_DISCRIMINATOR:
            ta_fragment = hydrator.parse_account(pubkey, data)
            payload = ta_fragment.payload
            ticks = [
                TickEntry(
                    initialized=bool(t["initialized"]),
                    liquidity_net=int(t["liquidity_net"]),
                    liquidity_gross=int(t["liquidity_gross"]),
                    fee_growth_outside_a=int(t["fee_growth_outside_a"]),
                    fee_growth_outside_b=int(t["fee_growth_outside_b"]),
                )
                for t in payload["ticks"]
            ]
            tick_arrays.append(
                TickArrayState(
                    pubkey=pubkey,
                    start_tick_index=int(payload["start_tick_index"]),
                    ticks=ticks,
                )
            )
    if pool_payload is None:
        raise ValueError(
            f"corpus slot {corpus_slot} has no pool fixture for {pool_pubkey!r}"
        )

    token_program_path_gz = root / f"program_accounts-{SPL_TOKEN_PROGRAM}.json.gz"
    token_program_path = root / f"program_accounts-{SPL_TOKEN_PROGRAM}.json"
    vault_a_amount = 0
    vault_b_amount = 0
    if token_program_path_gz.exists() or token_program_path.exists():
        spl_fixture = _read_program_fixture(
            token_program_path_gz if token_program_path_gz.exists() else token_program_path
        )
        for entry in spl_fixture.get("accounts", []):
            pubkey = str(entry.get("pubkey", ""))
            data = _decode_account_data(entry.get("account", {}).get("data"))
            if not data or len(data) < 72:
                continue
            payload = parse_token_vault_account(pubkey, data)
            if pubkey == pool_payload.get("token_vault_a"):
                vault_a_amount = int(payload["amount"])
            elif pubkey == pool_payload.get("token_vault_b"):
                vault_b_amount = int(payload["amount"])

    token_a = _resolve_token(
        str(pool_payload["token_mint_a"]),
        token_id_override=token_a_id,
        token_symbol_override=token_a_symbol,
    )
    token_b = _resolve_token(
        str(pool_payload["token_mint_b"]),
        token_id_override=token_b_id,
        token_symbol_override=token_b_symbol,
    )

    pool_liquidity = int(pool_payload["liquidity"])
    if initial_liquidity is not None and initial_liquidity > 0:
        # ``initial_liquidity`` is interpreted as the target token-B vault
        # depth in human units (decimals stripped) — e.g., ``initial_liquidity=
        # 1_000_000`` on a SOL/USDC pool means $1M USDC of depth. Scale
        # ``pool.liquidity``, both vaults, and per-tick liquidity_net/gross by
        # ``target_b / captured_b`` so the price (sqrt_price_x64) and tick
        # distribution are preserved while depth tracks the slider. Per-tick
        # floor-rounding introduces O(num_ticks) drift in position
        # entry+exit cancellation, which is negligible against L values in
        # the 1e10+ range.
        target_b = int(float(initial_liquidity) * (10 ** token_b.decimals))
        if vault_b_amount <= 0:
            raise ValueError(
                f"cannot scale whirlpool depth: corpus slot {corpus_slot} has "
                f"empty token_b vault for pool {pool_pubkey!r}"
            )
        num, den = target_b, vault_b_amount
        if num != den:
            vault_a_amount = _scale_signed(vault_a_amount, num, den)
            vault_b_amount = target_b
            pool_liquidity = _scale_signed(pool_liquidity, num, den)
            tick_arrays = [
                TickArrayState(
                    pubkey=ta.pubkey,
                    start_tick_index=ta.start_tick_index,
                    ticks=[
                        TickEntry(
                            initialized=t.initialized,
                            liquidity_net=_scale_signed(t.liquidity_net, num, den),
                            liquidity_gross=_scale_signed(t.liquidity_gross, num, den),
                            fee_growth_outside_a=t.fee_growth_outside_a,
                            fee_growth_outside_b=t.fee_growth_outside_b,
                        )
                        for t in ta.ticks
                    ],
                )
                for ta in tick_arrays
            ]

    pool_state = WhirlpoolPoolState(
        pubkey=str(pool_payload["pubkey"]),
        tick_spacing=int(pool_payload["tick_spacing"]),
        fee_rate=int(fee_rate_override if fee_rate_override is not None else pool_payload["fee_rate"]),
        protocol_fee_rate=int(pool_payload["protocol_fee_rate"]),
        liquidity=pool_liquidity,
        sqrt_price_x64=int(pool_payload["sqrt_price_x64"]),
        tick_current_index=int(pool_payload["tick_current_index"]),
        fee_growth_global_a=int(pool_payload.get("fee_growth_global_a", 0)),
        fee_growth_global_b=int(pool_payload.get("fee_growth_global_b", 0)),
        protocol_fee_owed_a=int(pool_payload.get("protocol_fee_owed_a", 0)),
        protocol_fee_owed_b=int(pool_payload.get("protocol_fee_owed_b", 0)),
        token_mint_a=str(pool_payload["token_mint_a"]),
        token_mint_b=str(pool_payload["token_mint_b"]),
        token_vault_a_pubkey=str(pool_payload["token_vault_a"]),
        token_vault_b_pubkey=str(pool_payload["token_vault_b"]),
        token_vault_a_amount=vault_a_amount,
        token_vault_b_amount=vault_b_amount,
        token_decimals_a=token_a.decimals,
        token_decimals_b=token_b.decimals,
    )

    return WhirlpoolMarket(
        pool=pool_state,
        tick_arrays=tick_arrays,
        token_a=token_a,
        token_b=token_b,
        pool_account_id=pool_account_id or pool_state.pubkey,
        fee_model=fee_model,
    )
