"""Jupiter Perps fixture parser and model scope.

This module parses selected Jupiter Perps ``Custody`` and ``Position``
accounts into Phase 2 ``InitialStateFragment`` records. It intentionally
stays fixture-scoped: production stress calibration still requires exact
as-of-slot account and oracle fixtures before these fragments can support
mainnet-accuracy claims.

Supported account formats come from Jupiter's Perps developer docs and the
linked Anchor IDL sample as of 2026-05-03:

* ``Custody`` discriminator ``01b830515d833f91``.
* ``Position`` discriminator ``aabc8fe47a40f7d0``.

The parser keeps the fields the Phase 2 engine needs to define the model
boundary: market/custody identifiers, collateral and position notional,
oracle references, funding counters, and liquidation-relevant values.
Full perp stress calibration remains gated on real account/oracle fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator
from defi_sim_solana.program_ids import JUPITER_PERPS_PROGRAM

__all__ = [
    "JUPITER_PERPS_CUSTODY_DISCRIMINATOR",
    "JUPITER_PERPS_POSITION_DISCRIMINATOR",
    "JUPITER_PERPS_PROTOCOL_MODEL",
    "JupiterPerpsCustody",
    "JupiterPerpsHydrator",
    "JupiterPerpsPosition",
]


JUPITER_PERPS_PROTOCOL_MODEL = "JupiterPerps"
JUPITER_PERPS_CUSTODY_DISCRIMINATOR = bytes(
    (1, 184, 48, 81, 93, 131, 63, 145)
)
JUPITER_PERPS_POSITION_DISCRIMINATOR = bytes(
    (170, 188, 143, 228, 122, 64, 247, 208)
)

_CUSTODY_MIN_LEN = 296
_POSITION_MIN_LEN = 210

_ORACLE_TYPE_NAMES = {
    0: "none",
    1: "test",
    2: "pyth",
}
_SIDE_NAMES = {
    0: "none",
    1: "long",
    2: "short",
}


@dataclass(frozen=True, slots=True)
class JupiterPerpsCustody:
    """Typed view of a Jupiter Perps ``Custody`` account subset."""

    pubkey: str
    pool: str
    mint: str
    token_account: str
    decimals: int
    is_stable: bool
    oracle_account: str
    oracle_type: str
    max_price_error: int
    max_price_age_sec: int
    trade_spread_long: int
    trade_spread_short: int
    swap_spread: int
    max_leverage: int
    max_global_long_sizes: int
    max_global_short_sizes: int
    target_ratio_bps: int
    fees_reserves: int
    owned: int
    locked: int
    guaranteed_usd: int
    global_short_sizes: int
    global_short_average_prices: int
    cumulative_interest_rate: int
    funding_last_update: int
    hourly_funding_dbps: int
    allow_increase_position: bool
    allow_decrease_position: bool
    allow_collateral_withdrawal: bool
    allow_liquidate_position: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "account_type": "custody",
            "pool": self.pool,
            "mint": self.mint,
            "token_account": self.token_account,
            "decimals": self.decimals,
            "is_stable": self.is_stable,
            "oracle_references": [self.oracle_account],
            "oracle": {
                "oracle_account": self.oracle_account,
                "oracle_type": self.oracle_type,
                "max_price_error": self.max_price_error,
                "max_price_age_sec": self.max_price_age_sec,
            },
            "pricing": {
                "trade_spread_long": self.trade_spread_long,
                "trade_spread_short": self.trade_spread_short,
                "swap_spread": self.swap_spread,
                "max_leverage": self.max_leverage,
                "max_global_long_sizes": self.max_global_long_sizes,
                "max_global_short_sizes": self.max_global_short_sizes,
            },
            "permissions": {
                "allow_increase_position": self.allow_increase_position,
                "allow_decrease_position": self.allow_decrease_position,
                "allow_collateral_withdrawal": self.allow_collateral_withdrawal,
                "allow_liquidate_position": self.allow_liquidate_position,
            },
            "target_ratio_bps": self.target_ratio_bps,
            "assets": {
                "fees_reserves": self.fees_reserves,
                "owned": self.owned,
                "locked": self.locked,
                "guaranteed_usd": self.guaranteed_usd,
                "global_short_sizes": self.global_short_sizes,
                "global_short_average_prices": self.global_short_average_prices,
            },
            "funding_rate_state": {
                "cumulative_interest_rate": self.cumulative_interest_rate,
                "last_update": self.funding_last_update,
                "hourly_funding_dbps": self.hourly_funding_dbps,
            },
            "liquidation_calibration": "unsupported_without_real_account_oracle_fixtures",
        }


@dataclass(frozen=True, slots=True)
class JupiterPerpsPosition:
    """Typed view of a Jupiter Perps ``Position`` account subset."""

    pubkey: str
    owner: str
    pool: str
    custody: str
    collateral_custody: str
    open_time: int
    update_time: int
    side: str
    price: int
    size_usd: int
    collateral_usd: int
    realised_pnl_usd: int
    cumulative_interest_snapshot: int
    locked_amount: int

    @property
    def is_closed(self) -> bool:
        return self.size_usd == 0

    @property
    def margin_ratio_bps(self) -> int | None:
        if self.size_usd <= 0:
            return None
        return self.collateral_usd * 10_000 // self.size_usd

    def to_payload(self) -> dict[str, object]:
        return {
            "account_type": "position",
            "pool": self.pool,
            "custody": self.custody,
            "collateral_custody": self.collateral_custody,
            "open_time": self.open_time,
            "update_time": self.update_time,
            "side": self.side,
            "price": self.price,
            "size_usd": self.size_usd,
            "collateral_usd": self.collateral_usd,
            "realised_pnl_usd": self.realised_pnl_usd,
            "cumulative_interest_snapshot": self.cumulative_interest_snapshot,
            "locked_amount": self.locked_amount,
            "is_closed": self.is_closed,
            "margin_ratio_bps": self.margin_ratio_bps,
            "liquidation_state": "requires_oracle_and_custody_state",
            "stress_calibration": "unsupported_without_real_account_oracle_fixtures",
        }


class JupiterPerpsHydrator(StateHydrator):
    """Parser for selected Jupiter Perps custody and position fixtures."""

    program_id: str = JUPITER_PERPS_PROGRAM
    schema_version: int = 1

    def account_filters(self) -> list[AccountFilter]:
        return [
            AccountFilter(discriminator=JUPITER_PERPS_CUSTODY_DISCRIMINATOR),
            AccountFilter(discriminator=JUPITER_PERPS_POSITION_DISCRIMINATOR),
        ]

    def oracle_dependencies(self) -> list[str]:
        return []

    def parse_custody(self, pubkey: str, data: bytes) -> JupiterPerpsCustody:
        if len(data) < _CUSTODY_MIN_LEN:
            raise ValueError(
                f"Jupiter Perps Custody account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_CUSTODY_MIN_LEN} bytes."
            )
        _require_discriminator(data, JUPITER_PERPS_CUSTODY_DISCRIMINATOR, pubkey)

        oracle = 106
        pricing = 151
        permissions = 199
        assets = 214
        funding = 262

        return JupiterPerpsCustody(
            pubkey=pubkey,
            pool=_pubkey(data, 8, pubkey),
            mint=_pubkey(data, 40, pubkey),
            token_account=_pubkey(data, 72, pubkey),
            decimals=_u8(data, 104, pubkey),
            is_stable=_bool(data, 105, pubkey),
            oracle_account=_pubkey(data, oracle, pubkey),
            oracle_type=_ORACLE_TYPE_NAMES.get(_u8(data, oracle + 32, pubkey), "unknown"),
            max_price_error=_u64(data, oracle + 33, pubkey),
            max_price_age_sec=_u32(data, oracle + 41, pubkey),
            trade_spread_long=_u64(data, pricing, pubkey),
            trade_spread_short=_u64(data, pricing + 8, pubkey),
            swap_spread=_u64(data, pricing + 16, pubkey),
            max_leverage=_u64(data, pricing + 24, pubkey),
            max_global_long_sizes=_u64(data, pricing + 32, pubkey),
            max_global_short_sizes=_u64(data, pricing + 40, pubkey),
            allow_increase_position=_bool(data, permissions + 3, pubkey),
            allow_decrease_position=_bool(data, permissions + 4, pubkey),
            allow_collateral_withdrawal=_bool(data, permissions + 5, pubkey),
            allow_liquidate_position=_bool(data, permissions + 6, pubkey),
            target_ratio_bps=_u64(data, 206, pubkey),
            fees_reserves=_u64(data, assets, pubkey),
            owned=_u64(data, assets + 8, pubkey),
            locked=_u64(data, assets + 16, pubkey),
            guaranteed_usd=_u64(data, assets + 24, pubkey),
            global_short_sizes=_u64(data, assets + 32, pubkey),
            global_short_average_prices=_u64(data, assets + 40, pubkey),
            cumulative_interest_rate=_u128(data, funding, pubkey),
            funding_last_update=_i64(data, funding + 16, pubkey),
            hourly_funding_dbps=_u64(data, funding + 24, pubkey),
        )

    def parse_position(self, pubkey: str, data: bytes) -> JupiterPerpsPosition:
        if len(data) < _POSITION_MIN_LEN:
            raise ValueError(
                f"Jupiter Perps Position account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_POSITION_MIN_LEN} bytes."
            )
        _require_discriminator(data, JUPITER_PERPS_POSITION_DISCRIMINATOR, pubkey)

        return JupiterPerpsPosition(
            pubkey=pubkey,
            owner=_pubkey(data, 8, pubkey),
            pool=_pubkey(data, 40, pubkey),
            custody=_pubkey(data, 72, pubkey),
            collateral_custody=_pubkey(data, 104, pubkey),
            open_time=_i64(data, 136, pubkey),
            update_time=_i64(data, 144, pubkey),
            side=_SIDE_NAMES.get(_u8(data, 152, pubkey), "unknown"),
            price=_u64(data, 153, pubkey),
            size_usd=_u64(data, 161, pubkey),
            collateral_usd=_u64(data, 169, pubkey),
            realised_pnl_usd=_i64(data, 177, pubkey),
            cumulative_interest_snapshot=_u128(data, 185, pubkey),
            locked_amount=_u64(data, 201, pubkey),
        )

    def parse_account(self, pubkey: str, data: bytes) -> InitialStateFragment:
        if data.startswith(JUPITER_PERPS_CUSTODY_DISCRIMINATOR):
            custody = self.parse_custody(pubkey, data)
            return InitialStateFragment(
                kind="perp_market",
                protocol_model=JUPITER_PERPS_PROTOCOL_MODEL,
                pubkey=pubkey,
                owner=None,
                payload=custody.to_payload(),
            )
        if data.startswith(JUPITER_PERPS_POSITION_DISCRIMINATOR):
            position = self.parse_position(pubkey, data)
            return InitialStateFragment(
                kind="perp_position",
                protocol_model=JUPITER_PERPS_PROTOCOL_MODEL,
                pubkey=pubkey,
                owner=position.owner,
                payload=position.to_payload(),
            )
        raise ValueError(
            f"Jupiter Perps account {pubkey!r} has unsupported discriminator "
            f"{data[:8].hex()}."
        )


def _require_discriminator(data: bytes, expected: bytes, pubkey: str) -> None:
    if data[:8] != expected:
        raise ValueError(
            f"Jupiter Perps account {pubkey!r} has discriminator {data[:8].hex()}; "
            f"expected {expected.hex()}."
        )


def _pubkey(data: bytes, offset: int, pubkey: str) -> str:
    _require_len(data, offset, 32, pubkey)
    return _encode_base58(data[offset : offset + 32])


def _bool(data: bytes, offset: int, pubkey: str) -> bool:
    return _u8(data, offset, pubkey) > 0


def _u8(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 1, False, pubkey)


def _u32(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 4, False, pubkey)


def _u64(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 8, False, pubkey)


def _i64(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 8, True, pubkey)


def _u128(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 16, False, pubkey)


def _int(data: bytes, offset: int, length: int, signed: bool, pubkey: str) -> int:
    _require_len(data, offset, length, pubkey)
    return int.from_bytes(data[offset : offset + length], "little", signed=signed)


def _require_len(data: bytes, offset: int, length: int, pubkey: str) -> None:
    if offset + length > len(data):
        raise ValueError(
            f"Jupiter Perps account {pubkey!r} is too short to read {length} bytes "
            f"at offset {offset}."
        )


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _encode_base58(raw: bytes) -> str:
    if not raw:
        return ""
    num = int.from_bytes(raw, "big")
    encoded = ""
    while num:
        num, remainder = divmod(num, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading_zeroes + (encoded or "")
