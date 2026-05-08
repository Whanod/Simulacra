"""Kamino Lend parser and decoder tests for FIX-012."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from defi_sim_solana.program_ids import KAMINO_LEND_PROGRAM, TOKEN_PROGRAM
from defi_sim_solana.replay.kamino_lend import (
    KAMINO_LEND_OBLIGATION_DISCRIMINATOR,
    KAMINO_LEND_PROTOCOL_MODEL,
    KAMINO_LEND_RESERVE_DISCRIMINATOR,
    KaminoLendHydrator,
)
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    KaminoBorrowAction,
    KaminoDepositAction,
    KaminoLiquidateAction,
    KaminoRepayAction,
    KaminoWithdrawAction,
    PartialDecodedAction,
    action_decode_status,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _encode_base58(raw: bytes) -> str:
    num = int.from_bytes(raw, "big")
    encoded = ""
    while num:
        num, remainder = divmod(num, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading_zeroes + encoded


def _pubkey(seed: int) -> bytes:
    return bytes((seed,)) * 32


def _pubkey_str(seed: int) -> str:
    return _encode_base58(_pubkey(seed))


def _write_pubkey(buf: bytearray, offset: int, seed: int) -> None:
    buf[offset : offset + 32] = _pubkey(seed)


def _write_int(buf: bytearray, offset: int, value: int, length: int) -> None:
    buf[offset : offset + length] = value.to_bytes(length, "little")


def _reserve_fixture() -> bytes:
    buf = bytearray(5_200)
    buf[:8] = KAMINO_LEND_RESERVE_DISCRIMINATOR
    _write_int(buf, 8, 1, 8)
    _write_int(buf, 16, 417_309_540, 8)
    _write_pubkey(buf, 32, 2)

    liquidity = 128
    _write_pubkey(buf, liquidity, 3)
    _write_pubkey(buf, liquidity + 32, 4)
    _write_pubkey(buf, liquidity + 64, 5)
    _write_int(buf, liquidity + 96, 1_000_000, 8)
    _write_int(buf, liquidity + 104, 250_000, 16)
    _write_int(buf, liquidity + 120, 25_000_000_000, 16)
    _write_int(buf, liquidity + 136, 1_700_000_000, 8)
    _write_int(buf, liquidity + 144, 6, 8)

    collateral = 2432
    _write_pubkey(buf, collateral, 6)
    _write_int(buf, collateral + 32, 900_000, 8)
    _write_pubkey(buf, collateral + 40, 7)

    config = 4728
    buf[config + 14] = 10
    buf[config + 15] = 20
    buf[config + 16] = 65
    buf[config + 17] = 80
    _write_int(buf, config + 18, 300, 2)
    _write_int(buf, config + 20, 500, 2)
    _write_int(buf, config + 152, 115, 8)
    _write_int(buf, config + 160, 10_000_000, 8)
    _write_int(buf, config + 168, 4_000_000, 8)

    token_info = config + 176
    _write_pubkey(buf, token_info + 80, 8)   # Scope price feed
    _write_pubkey(buf, token_info + 128, 9)  # Switchboard price
    _write_pubkey(buf, token_info + 192, 10)  # Pyth price
    return bytes(buf)


def _obligation_fixture() -> bytes:
    buf = bytearray(2_500)
    buf[:8] = KAMINO_LEND_OBLIGATION_DISCRIMINATOR
    _write_int(buf, 8, 1, 8)
    _write_int(buf, 16, 417_309_541, 8)
    _write_pubkey(buf, 32, 2)
    _write_pubkey(buf, 64, 11)

    deposit = 96
    _write_pubkey(buf, deposit, 12)
    _write_int(buf, deposit + 32, 800_000, 8)
    _write_int(buf, deposit + 40, 2_100_000, 16)
    _write_int(buf, deposit + 56, 50_000, 8)
    _write_int(buf, 1192, 2_100_000, 16)

    borrow = 1208
    _write_pubkey(buf, borrow, 13)
    _write_int(buf, borrow + 80, 1_700_000_100, 8)
    _write_int(buf, borrow + 88, 1_900_000, 16)
    _write_int(buf, borrow + 104, 1_950_000, 16)
    _write_int(buf, borrow + 120, 2_200_000, 16)
    _write_int(buf, borrow + 136, 1_900_000, 8)
    _write_int(buf, 2208, 2_200_000, 16)
    _write_int(buf, 2224, 1_950_000, 16)
    _write_int(buf, 2240, 1_300_000, 16)
    _write_int(buf, 2256, 2_000_000, 16)
    buf[2285] = 1
    buf[2287] = 1
    buf[2320] = 0
    buf[2321] = 72
    buf[2322] = 75
    return bytes(buf)


def _legacy_tx(
    *,
    signatures: list[str],
    account_keys: list[str],
    instructions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "transaction": {
            "signatures": signatures,
            "message": {
                "accountKeys": account_keys,
                "instructions": instructions,
            },
        },
        "meta": {"computeUnitsConsumed": 40_000, "fee": 5_000},
    }


def _kamino_payload(discriminator: bytes, *amounts: int) -> list[str]:
    payload = discriminator + b"".join(
        amount.to_bytes(8, "little") for amount in amounts
    )
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _kamino_tx(
    *,
    discriminator: bytes,
    amounts: tuple[int, ...],
    accounts: list[str],
) -> dict[str, Any]:
    keys = [*accounts, KAMINO_LEND_PROGRAM]
    return _legacy_tx(
        signatures=["kamino-sig"],
        account_keys=keys,
        instructions=[
            {
                "programIdIndex": len(keys) - 1,
                "accounts": list(range(len(accounts))),
                "data": _kamino_payload(discriminator, *amounts),
            }
        ],
    )


def test_kamino_hydrator_program_id_schema_and_filters() -> None:
    hydrator = KaminoLendHydrator()

    assert hydrator.program_id == KAMINO_LEND_PROGRAM
    assert hydrator.schema_version == 1
    filters = hydrator.account_filters()
    assert [f.discriminator for f in filters] == [
        KAMINO_LEND_RESERVE_DISCRIMINATOR,
        KAMINO_LEND_OBLIGATION_DISCRIMINATOR,
    ]


def test_kamino_reserve_fixture_parses_to_lending_reserve_fragment() -> None:
    fragment = KaminoLendHydrator().parse_account(
        "KaminoReserveFixture",
        _reserve_fixture(),
    )

    assert fragment.kind == "lending_reserve"
    assert fragment.protocol_model == KAMINO_LEND_PROTOCOL_MODEL
    assert fragment.owner is None
    assert fragment.payload["liquidity_mint"] == _pubkey_str(3)
    assert fragment.payload["collateral_mint"] == _pubkey_str(6)
    assert fragment.payload["available_amount"] == 1_000_000
    assert fragment.payload["borrowed_amount_sf"] == 250_000
    assert fragment.payload["oracle_references"] == [
        _pubkey_str(8),
        _pubkey_str(9),
        _pubkey_str(10),
    ]
    assert fragment.payload["risk_parameters"] == {
        "loan_to_value_pct": 65,
        "liquidation_threshold_pct": 80,
        "min_liquidation_bonus_bps": 300,
        "max_liquidation_bonus_bps": 500,
        "borrow_factor_pct": 115,
        "deposit_limit": 10_000_000,
        "borrow_limit": 4_000_000,
        "protocol_take_rate_pct": 10,
        "protocol_liquidation_fee_pct": 20,
    }


def test_kamino_obligation_fixture_parses_to_lending_position_fragment() -> None:
    fragment = KaminoLendHydrator().parse_account(
        "KaminoObligationFixture",
        _obligation_fixture(),
    )

    assert fragment.kind == "lending_position"
    assert fragment.protocol_model == KAMINO_LEND_PROTOCOL_MODEL
    assert fragment.owner == _pubkey_str(11)
    assert fragment.payload["lending_market"] == _pubkey_str(2)
    assert fragment.payload["has_debt"] is True
    assert fragment.payload["is_liquidatable_by_values"] is True
    assert fragment.payload["collateral"] == [
        {
            "deposit_reserve": _pubkey_str(12),
            "deposited_amount": 800_000,
            "market_value_sf": 2_100_000,
            "borrowed_amount_against_this_collateral_in_elevation_group": 50_000,
        }
    ]
    assert fragment.payload["debt"][0]["borrow_reserve"] == _pubkey_str(13)
    assert fragment.payload["debt"][0]["borrowed_amount_sf"] == 1_900_000
    assert fragment.payload["unhealthy_borrow_value_sf"] == 2_000_000


@pytest.mark.parametrize(
    ("discriminator", "amounts", "accounts", "expected_type"),
    [
        (
            bytes((129, 199, 4, 2, 222, 39, 26, 46)),
            (123_000,),
            [
                "owner",
                "obligation",
                "market",
                "market-authority",
                "reserve",
                "liquidity-mint",
                "liquidity-supply",
                "collateral-mint",
                "destination-collateral",
                "source-liquidity",
                "placeholder",
                TOKEN_PROGRAM,
                TOKEN_PROGRAM,
                "sysvar",
            ],
            KaminoDepositAction,
        ),
        (
            bytes((37, 116, 205, 103, 243, 192, 92, 198)),
            (456_000,),
            [
                "owner",
                "obligation",
                "market",
                "market-authority",
                "reserve",
                "source-collateral",
                "destination-liquidity",
                TOKEN_PROGRAM,
                "sysvar",
            ],
            KaminoWithdrawAction,
        ),
        (
            bytes((121, 127, 18, 204, 73, 245, 225, 65)),
            (789_000,),
            [
                "owner",
                "obligation",
                "market",
                "market-authority",
                "reserve",
                "liquidity-mint",
                "source-liquidity",
                "fee-receiver",
                "destination-liquidity",
                "referrer",
                TOKEN_PROGRAM,
                "sysvar",
            ],
            KaminoBorrowAction,
        ),
        (
            bytes((145, 178, 13, 225, 76, 240, 147, 72)),
            (987_000,),
            [
                "owner",
                "obligation",
                "market",
                "reserve",
                "liquidity-mint",
                "destination-liquidity",
                "source-liquidity",
                TOKEN_PROGRAM,
                "sysvar",
            ],
            KaminoRepayAction,
        ),
        (
            bytes((177, 71, 154, 188, 226, 133, 74, 55)),
            (111_000, 95_000, 101),
            [
                "liquidator",
                "obligation",
                "market",
                "market-authority",
                "repay-reserve",
                "repay-mint",
                "repay-supply",
                "withdraw-reserve",
                "withdraw-mint",
                "withdraw-collateral-mint",
                "withdraw-collateral-supply",
                "withdraw-liquidity-supply",
                "withdraw-fee-receiver",
                "source-liquidity",
                "destination-collateral",
                "destination-liquidity",
                TOKEN_PROGRAM,
                TOKEN_PROGRAM,
                TOKEN_PROGRAM,
                "sysvar",
            ],
            KaminoLiquidateAction,
        ),
    ],
)
def test_supported_kamino_instructions_map_to_engine_actions(
    discriminator: bytes,
    amounts: tuple[int, ...],
    accounts: list[str],
    expected_type: type,
) -> None:
    tx = _kamino_tx(
        discriminator=discriminator,
        amounts=amounts,
        accounts=accounts,
    )
    snap = SlotSnapshot.from_raw({"slot": 160, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, expected_type)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == accounts[0]
    assert action.compute_unit_limit == 40_000
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.program_ids == (KAMINO_LEND_PROGRAM,)
    assert action.materialized_metadata.fee_lamports == 5_000
    assert getattr(action, "amount", getattr(action, "repay_amount", None)) == amounts[0]


def test_supported_kamino_action_metadata_preserves_lending_accounts() -> None:
    tx = _kamino_tx(
        discriminator=bytes((121, 127, 18, 204, 73, 245, 225, 65)),
        amounts=(789_000,),
        accounts=[
            "owner",
            "obligation",
            "market",
            "market-authority",
            "borrow-reserve",
            "liquidity-mint",
            "reserve-source-liquidity",
            "fee-receiver",
            "destination-liquidity",
            "referrer",
            TOKEN_PROGRAM,
            "sysvar",
        ],
    )
    snap = SlotSnapshot.from_raw({"slot": 161, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, KaminoBorrowAction)
    assert action.obligation_id == "obligation"
    assert action.reserve_id == "borrow-reserve"
    assert action.lending_market == "market"
    assert action.token == "liquidity-mint"
    assert action.amount == 789_000
    assert action.destination_token_account == "destination-liquidity"


def test_unsupported_kamino_instruction_remains_partial() -> None:
    tx = _kamino_tx(
        discriminator=b"notknown",
        amounts=(1,),
        accounts=["owner", "obligation"],
    )
    snap = SlotSnapshot.from_raw({"slot": 162, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (KAMINO_LEND_PROGRAM,)
