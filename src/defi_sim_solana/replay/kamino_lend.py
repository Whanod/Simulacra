"""Kamino Lend fixture parser and model scope.

This module parses selected Kamino Lend ``Reserve`` and ``Obligation``
accounts into Phase 2 ``InitialStateFragment`` records. It is intentionally
fixture-scoped: production old-slot hydration still requires an exact
as-of-slot account-state source before these fragments can support
calibration or liquidation accuracy claims.

Supported account formats come from the official Kamino ``klend-sdk``
codegen as of 2026-05-03:

* ``Reserve`` discriminator ``2bf2ccca1af73b7f``.
* ``Obligation`` discriminator ``a8ce8d6a584caca7``.

The parser keeps only the fields the Phase 2 engine needs to define the
model boundary: reserve liquidity/collateral identifiers, utilization
quantities, risk parameters, oracle references, and obligation collateral
and debt legs. Full market math and liquidation calibration remain gated on
real account/oracle fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator
from defi_sim_solana.program_ids import KAMINO_LEND_PROGRAM

__all__ = [
    "KAMINO_LEND_OBLIGATION_DISCRIMINATOR",
    "KAMINO_LEND_PROTOCOL_MODEL",
    "KAMINO_LEND_RESERVE_DISCRIMINATOR",
    "KaminoLendHydrator",
    "KaminoObligation",
    "KaminoObligationBorrow",
    "KaminoObligationCollateral",
    "KaminoReserve",
]


KAMINO_LEND_PROTOCOL_MODEL = "KaminoLend"
KAMINO_LEND_RESERVE_DISCRIMINATOR = bytes(
    (43, 242, 204, 202, 26, 247, 59, 127)
)
KAMINO_LEND_OBLIGATION_DISCRIMINATOR = bytes(
    (168, 206, 141, 106, 88, 76, 172, 167)
)

_DEFAULT_PUBKEY = "11111111111111111111111111111111"

_RESERVE_LIQUIDITY_OFFSET = 128
_RESERVE_COLLATERAL_OFFSET = 2432
_RESERVE_CONFIG_OFFSET = 4728
_RESERVE_TOKEN_INFO_OFFSET = _RESERVE_CONFIG_OFFSET + 176
_RESERVE_MIN_LEN = _RESERVE_TOKEN_INFO_OFFSET + 224

_OBLIGATION_DEPOSITS_OFFSET = 96
_OBLIGATION_COLLATERAL_SIZE = 136
_OBLIGATION_BORROWS_OFFSET = 1208
_OBLIGATION_LIQUIDITY_SIZE = 200
_OBLIGATION_MIN_LEN = 2344


@dataclass(frozen=True, slots=True)
class KaminoReserve:
    """Typed view of a Kamino ``Reserve`` account subset."""

    pubkey: str
    version: int
    last_update_slot: int
    lending_market: str
    liquidity_mint: str
    liquidity_supply_vault: str
    liquidity_fee_vault: str
    available_amount: int
    borrowed_amount_sf: int
    market_price_sf: int
    market_price_last_updated_ts: int
    mint_decimals: int
    collateral_mint: str
    collateral_supply_vault: str
    collateral_mint_total_supply: int
    loan_to_value_pct: int
    liquidation_threshold_pct: int
    min_liquidation_bonus_bps: int
    max_liquidation_bonus_bps: int
    borrow_factor_pct: int
    deposit_limit: int
    borrow_limit: int
    protocol_take_rate_pct: int
    protocol_liquidation_fee_pct: int
    oracle_references: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "account_type": "reserve",
            "version": self.version,
            "last_update_slot": self.last_update_slot,
            "lending_market": self.lending_market,
            "liquidity_mint": self.liquidity_mint,
            "liquidity_supply_vault": self.liquidity_supply_vault,
            "liquidity_fee_vault": self.liquidity_fee_vault,
            "available_amount": self.available_amount,
            "borrowed_amount_sf": self.borrowed_amount_sf,
            "market_price_sf": self.market_price_sf,
            "market_price_last_updated_ts": self.market_price_last_updated_ts,
            "mint_decimals": self.mint_decimals,
            "collateral_mint": self.collateral_mint,
            "collateral_supply_vault": self.collateral_supply_vault,
            "collateral_mint_total_supply": self.collateral_mint_total_supply,
            "risk_parameters": {
                "loan_to_value_pct": self.loan_to_value_pct,
                "liquidation_threshold_pct": self.liquidation_threshold_pct,
                "min_liquidation_bonus_bps": self.min_liquidation_bonus_bps,
                "max_liquidation_bonus_bps": self.max_liquidation_bonus_bps,
                "borrow_factor_pct": self.borrow_factor_pct,
                "deposit_limit": self.deposit_limit,
                "borrow_limit": self.borrow_limit,
                "protocol_take_rate_pct": self.protocol_take_rate_pct,
                "protocol_liquidation_fee_pct": self.protocol_liquidation_fee_pct,
            },
            "oracle_references": list(self.oracle_references),
            "liquidation_calibration": "unsupported_without_real_account_oracle_fixtures",
        }


@dataclass(frozen=True, slots=True)
class KaminoObligationCollateral:
    """One active collateral leg in a Kamino ``Obligation``."""

    deposit_reserve: str
    deposited_amount: int
    market_value_sf: int
    borrowed_amount_against_this_collateral_in_elevation_group: int

    def to_payload(self) -> dict[str, object]:
        return {
            "deposit_reserve": self.deposit_reserve,
            "deposited_amount": self.deposited_amount,
            "market_value_sf": self.market_value_sf,
            "borrowed_amount_against_this_collateral_in_elevation_group": (
                self.borrowed_amount_against_this_collateral_in_elevation_group
            ),
        }


@dataclass(frozen=True, slots=True)
class KaminoObligationBorrow:
    """One active debt leg in a Kamino ``Obligation``."""

    borrow_reserve: str
    first_borrowed_at_timestamp: int
    borrowed_amount_sf: int
    market_value_sf: int
    borrow_factor_adjusted_market_value_sf: int
    borrowed_amount_outside_elevation_groups: int

    def to_payload(self) -> dict[str, object]:
        return {
            "borrow_reserve": self.borrow_reserve,
            "first_borrowed_at_timestamp": self.first_borrowed_at_timestamp,
            "borrowed_amount_sf": self.borrowed_amount_sf,
            "market_value_sf": self.market_value_sf,
            "borrow_factor_adjusted_market_value_sf": (
                self.borrow_factor_adjusted_market_value_sf
            ),
            "borrowed_amount_outside_elevation_groups": (
                self.borrowed_amount_outside_elevation_groups
            ),
        }


@dataclass(frozen=True, slots=True)
class KaminoObligation:
    """Typed view of a Kamino ``Obligation`` account subset."""

    pubkey: str
    tag: int
    last_update_slot: int
    lending_market: str
    owner: str
    collateral: tuple[KaminoObligationCollateral, ...]
    debt: tuple[KaminoObligationBorrow, ...]
    deposited_value_sf: int
    borrow_factor_adjusted_debt_value_sf: int
    borrowed_assets_market_value_sf: int
    allowed_borrow_value_sf: int
    unhealthy_borrow_value_sf: int
    elevation_group: int
    has_debt: bool
    borrowing_disabled: bool
    autodeleverage_target_ltv_pct: int
    lowest_reserve_deposit_max_ltv_pct: int

    @property
    def is_liquidatable_by_values(self) -> bool:
        return (
            self.has_debt
            and self.borrow_factor_adjusted_debt_value_sf
            > self.unhealthy_borrow_value_sf
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "account_type": "obligation",
            "tag": self.tag,
            "last_update_slot": self.last_update_slot,
            "lending_market": self.lending_market,
            "collateral": [leg.to_payload() for leg in self.collateral],
            "debt": [leg.to_payload() for leg in self.debt],
            "deposited_value_sf": self.deposited_value_sf,
            "borrow_factor_adjusted_debt_value_sf": (
                self.borrow_factor_adjusted_debt_value_sf
            ),
            "borrowed_assets_market_value_sf": self.borrowed_assets_market_value_sf,
            "allowed_borrow_value_sf": self.allowed_borrow_value_sf,
            "unhealthy_borrow_value_sf": self.unhealthy_borrow_value_sf,
            "elevation_group": self.elevation_group,
            "has_debt": self.has_debt,
            "borrowing_disabled": self.borrowing_disabled,
            "autodeleverage_target_ltv_pct": self.autodeleverage_target_ltv_pct,
            "lowest_reserve_deposit_max_ltv_pct": (
                self.lowest_reserve_deposit_max_ltv_pct
            ),
            "is_liquidatable_by_values": self.is_liquidatable_by_values,
            "liquidation_calibration": "unsupported_without_real_account_oracle_fixtures",
        }


class KaminoLendHydrator(StateHydrator):
    """Parser for selected Kamino Lend reserve and obligation fixtures."""

    program_id: str = KAMINO_LEND_PROGRAM
    schema_version: int = 1

    def account_filters(self) -> list[AccountFilter]:
        return [
            AccountFilter(discriminator=KAMINO_LEND_RESERVE_DISCRIMINATOR),
            AccountFilter(discriminator=KAMINO_LEND_OBLIGATION_DISCRIMINATOR),
        ]

    def oracle_dependencies(self) -> list[str]:
        return []

    def parse_reserve(self, pubkey: str, data: bytes) -> KaminoReserve:
        if len(data) < _RESERVE_MIN_LEN:
            raise ValueError(
                f"Kamino Reserve account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_RESERVE_MIN_LEN} bytes."
            )
        _require_discriminator(data, KAMINO_LEND_RESERVE_DISCRIMINATOR, pubkey)

        liquidity = _RESERVE_LIQUIDITY_OFFSET
        collateral = _RESERVE_COLLATERAL_OFFSET
        config = _RESERVE_CONFIG_OFFSET
        token_info = _RESERVE_TOKEN_INFO_OFFSET
        oracle_references = _oracle_references(data, token_info)

        return KaminoReserve(
            pubkey=pubkey,
            version=_u64(data, 8, pubkey),
            last_update_slot=_u64(data, 16, pubkey),
            lending_market=_pubkey(data, 32, pubkey),
            liquidity_mint=_pubkey(data, liquidity, pubkey),
            liquidity_supply_vault=_pubkey(data, liquidity + 32, pubkey),
            liquidity_fee_vault=_pubkey(data, liquidity + 64, pubkey),
            available_amount=_u64(data, liquidity + 96, pubkey),
            borrowed_amount_sf=_u128(data, liquidity + 104, pubkey),
            market_price_sf=_u128(data, liquidity + 120, pubkey),
            market_price_last_updated_ts=_u64(data, liquidity + 136, pubkey),
            mint_decimals=_u64(data, liquidity + 144, pubkey),
            collateral_mint=_pubkey(data, collateral, pubkey),
            collateral_mint_total_supply=_u64(data, collateral + 32, pubkey),
            collateral_supply_vault=_pubkey(data, collateral + 40, pubkey),
            protocol_take_rate_pct=_u8(data, config + 14, pubkey),
            protocol_liquidation_fee_pct=_u8(data, config + 15, pubkey),
            loan_to_value_pct=_u8(data, config + 16, pubkey),
            liquidation_threshold_pct=_u8(data, config + 17, pubkey),
            min_liquidation_bonus_bps=_u16(data, config + 18, pubkey),
            max_liquidation_bonus_bps=_u16(data, config + 20, pubkey),
            borrow_factor_pct=_u64(data, config + 152, pubkey),
            deposit_limit=_u64(data, config + 160, pubkey),
            borrow_limit=_u64(data, config + 168, pubkey),
            oracle_references=oracle_references,
        )

    def parse_obligation(self, pubkey: str, data: bytes) -> KaminoObligation:
        if len(data) < _OBLIGATION_MIN_LEN:
            raise ValueError(
                f"Kamino Obligation account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_OBLIGATION_MIN_LEN} bytes."
            )
        _require_discriminator(
            data,
            KAMINO_LEND_OBLIGATION_DISCRIMINATOR,
            pubkey,
        )

        collateral = tuple(
            leg
            for leg in (
                _parse_obligation_collateral(
                    data,
                    _OBLIGATION_DEPOSITS_OFFSET
                    + idx * _OBLIGATION_COLLATERAL_SIZE,
                    pubkey,
                )
                for idx in range(8)
            )
            if leg is not None
        )
        debt = tuple(
            leg
            for leg in (
                _parse_obligation_borrow(
                    data,
                    _OBLIGATION_BORROWS_OFFSET
                    + idx * _OBLIGATION_LIQUIDITY_SIZE,
                    pubkey,
                )
                for idx in range(5)
            )
            if leg is not None
        )

        return KaminoObligation(
            pubkey=pubkey,
            tag=_u64(data, 8, pubkey),
            last_update_slot=_u64(data, 16, pubkey),
            lending_market=_pubkey(data, 32, pubkey),
            owner=_pubkey(data, 64, pubkey),
            collateral=collateral,
            debt=debt,
            deposited_value_sf=_u128(data, 1192, pubkey),
            borrow_factor_adjusted_debt_value_sf=_u128(data, 2208, pubkey),
            borrowed_assets_market_value_sf=_u128(data, 2224, pubkey),
            allowed_borrow_value_sf=_u128(data, 2240, pubkey),
            unhealthy_borrow_value_sf=_u128(data, 2256, pubkey),
            elevation_group=_u8(data, 2285, pubkey),
            has_debt=_u8(data, 2287, pubkey) > 0,
            borrowing_disabled=_u8(data, 2320, pubkey) > 0,
            autodeleverage_target_ltv_pct=_u8(data, 2321, pubkey),
            lowest_reserve_deposit_max_ltv_pct=_u8(data, 2322, pubkey),
        )

    def parse_account(self, pubkey: str, data: bytes) -> InitialStateFragment:
        if data.startswith(KAMINO_LEND_RESERVE_DISCRIMINATOR):
            reserve = self.parse_reserve(pubkey, data)
            return InitialStateFragment(
                kind="lending_reserve",
                protocol_model=KAMINO_LEND_PROTOCOL_MODEL,
                pubkey=pubkey,
                owner=None,
                payload=reserve.to_payload(),
            )
        if data.startswith(KAMINO_LEND_OBLIGATION_DISCRIMINATOR):
            obligation = self.parse_obligation(pubkey, data)
            return InitialStateFragment(
                kind="lending_position",
                protocol_model=KAMINO_LEND_PROTOCOL_MODEL,
                pubkey=pubkey,
                owner=obligation.owner,
                payload=obligation.to_payload(),
            )
        raise ValueError(
            f"Kamino account {pubkey!r} has unsupported discriminator "
            f"{data[:8].hex()}."
        )


def _parse_obligation_collateral(
    data: bytes,
    offset: int,
    pubkey: str,
) -> KaminoObligationCollateral | None:
    deposit_reserve = _pubkey(data, offset, pubkey)
    deposited_amount = _u64(data, offset + 32, pubkey)
    if deposit_reserve == _DEFAULT_PUBKEY and deposited_amount == 0:
        return None
    return KaminoObligationCollateral(
        deposit_reserve=deposit_reserve,
        deposited_amount=deposited_amount,
        market_value_sf=_u128(data, offset + 40, pubkey),
        borrowed_amount_against_this_collateral_in_elevation_group=_u64(
            data,
            offset + 56,
            pubkey,
        ),
    )


def _parse_obligation_borrow(
    data: bytes,
    offset: int,
    pubkey: str,
) -> KaminoObligationBorrow | None:
    borrow_reserve = _pubkey(data, offset, pubkey)
    borrowed_amount_sf = _u128(data, offset + 88, pubkey)
    if borrow_reserve == _DEFAULT_PUBKEY and borrowed_amount_sf == 0:
        return None
    return KaminoObligationBorrow(
        borrow_reserve=borrow_reserve,
        first_borrowed_at_timestamp=_u64(data, offset + 80, pubkey),
        borrowed_amount_sf=borrowed_amount_sf,
        market_value_sf=_u128(data, offset + 104, pubkey),
        borrow_factor_adjusted_market_value_sf=_u128(
            data,
            offset + 120,
            pubkey,
        ),
        borrowed_amount_outside_elevation_groups=_u64(
            data,
            offset + 136,
            pubkey,
        ),
    )


def _oracle_references(data: bytes, token_info_offset: int) -> tuple[str, ...]:
    offsets = (
        token_info_offset + 80,
        token_info_offset + 128,
        token_info_offset + 160,
        token_info_offset + 192,
    )
    refs: list[str] = []
    for offset in offsets:
        value = _encode_base58(data[offset : offset + 32])
        if value != _DEFAULT_PUBKEY and value not in refs:
            refs.append(value)
    return tuple(refs)


def _require_discriminator(data: bytes, expected: bytes, pubkey: str) -> None:
    if data[:8] != expected:
        raise ValueError(
            f"Kamino account {pubkey!r} has discriminator {data[:8].hex()}; "
            f"expected {expected.hex()}."
        )


def _pubkey(data: bytes, offset: int, pubkey: str) -> str:
    _require_len(data, offset, 32, pubkey)
    return _encode_base58(data[offset : offset + 32])


def _u8(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 1, False, pubkey)


def _u16(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 2, False, pubkey)


def _u64(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 8, False, pubkey)


def _u128(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 16, False, pubkey)


def _int(data: bytes, offset: int, length: int, signed: bool, pubkey: str) -> int:
    _require_len(data, offset, length, pubkey)
    return int.from_bytes(data[offset : offset + length], "little", signed=signed)


def _require_len(data: bytes, offset: int, length: int, pubkey: str) -> None:
    if offset + length > len(data):
        raise ValueError(
            f"Kamino account {pubkey!r} is too short to read {length} bytes "
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
