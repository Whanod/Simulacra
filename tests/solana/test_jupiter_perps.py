"""Jupiter Perps parser and decoder tests for FIX-013."""

from __future__ import annotations

import base64
from typing import Any

from defi_sim.core.types import MarginDirection, PositionSide
from defi_sim_solana.program_ids import JUPITER_PERPS_PROGRAM, TOKEN_PROGRAM
from defi_sim_solana.replay.jupiter_perps import (
    JUPITER_PERPS_CUSTODY_DISCRIMINATOR,
    JUPITER_PERPS_POSITION_DISCRIMINATOR,
    JUPITER_PERPS_PROTOCOL_MODEL,
    JupiterPerpsHydrator,
)
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    JupiterPerpsAdjustMarginAction,
    JupiterPerpsClosePositionAction,
    JupiterPerpsLiquidateAction,
    JupiterPerpsOpenPositionAction,
    JupiterPerpsOracleReadAction,
    PartialDecodedAction,
    action_decode_status,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot


_CREATE_INCREASE_POSITION_REQUEST = bytes((8, 160, 201, 226, 217, 74, 228, 137))
_CREATE_DECREASE_POSITION_REQUEST = bytes((146, 21, 51, 121, 187, 208, 7, 69))
_CLOSE_POSITION_REQUEST = bytes((40, 105, 217, 188, 220, 45, 109, 110))
_LIQUIDATE_FULL_POSITION2 = bytes((233, 160, 187, 98, 2, 234, 48, 249))
_REFRESH_ASSETS_UNDER_MANAGEMENT = bytes((162, 0, 215, 55, 225, 15, 185, 0))

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


def _write_int(
    buf: bytearray,
    offset: int,
    value: int,
    length: int,
    *,
    signed: bool = False,
) -> None:
    buf[offset : offset + length] = value.to_bytes(
        length,
        "little",
        signed=signed,
    )


def _custody_fixture() -> bytes:
    buf = bytearray(296)
    buf[:8] = JUPITER_PERPS_CUSTODY_DISCRIMINATOR
    _write_pubkey(buf, 8, 2)
    _write_pubkey(buf, 40, 3)
    _write_pubkey(buf, 72, 4)
    buf[104] = 6
    buf[105] = 0
    oracle = 106
    _write_pubkey(buf, oracle, 5)
    buf[oracle + 32] = 2
    _write_int(buf, oracle + 33, 25, 8)
    _write_int(buf, oracle + 41, 60, 4)

    pricing = 151
    for idx, value in enumerate((4, 5, 6, 250, 9_000_000, 8_000_000)):
        _write_int(buf, pricing + idx * 8, value, 8)

    permissions = 199
    buf[permissions : permissions + 7] = bytes((1, 1, 1, 1, 1, 0, 1))
    _write_int(buf, 206, 2_500, 8)

    assets = 214
    for idx, value in enumerate((10, 1_000_000, 200_000, 3_000_000, 4_000_000, 5)):
        _write_int(buf, assets + idx * 8, value, 8)

    funding = 262
    _write_int(buf, funding, 123_456_789, 16)
    _write_int(buf, funding + 16, 1_700_000_000, 8, signed=True)
    _write_int(buf, funding + 24, 42, 8)
    buf[294] = 7
    buf[295] = 8
    return bytes(buf)


def _position_fixture() -> bytes:
    buf = bytearray(210)
    buf[:8] = JUPITER_PERPS_POSITION_DISCRIMINATOR
    _write_pubkey(buf, 8, 10)
    _write_pubkey(buf, 40, 2)
    _write_pubkey(buf, 72, 11)
    _write_pubkey(buf, 104, 12)
    _write_int(buf, 136, 1_700_000_010, 8, signed=True)
    _write_int(buf, 144, 1_700_000_020, 8, signed=True)
    buf[152] = 1
    _write_int(buf, 153, 158_225_872, 8)
    _write_int(buf, 161, 10_000_000, 8)
    _write_int(buf, 169, 2_000_000, 8)
    _write_int(buf, 177, -125_000, 8, signed=True)
    _write_int(buf, 185, 999_000, 16)
    _write_int(buf, 201, 700_000, 8)
    buf[209] = 9
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
        "meta": {"computeUnitsConsumed": 55_000, "fee": 5_000},
    }


def _u64(value: int) -> bytes:
    return value.to_bytes(8, "little")


def _opt_u64(value: int | None) -> bytes:
    if value is None:
        return b"\x00"
    return b"\x01" + _u64(value)


def _opt_bool(value: bool | None) -> bytes:
    if value is None:
        return b"\x00"
    return b"\x01" + bytes((1 if value else 0,))


def _jupiter_payload(raw: bytes) -> list[str]:
    return [base64.b64encode(raw).decode("ascii"), "base64"]


def _perps_tx(*, data: bytes, accounts: list[str]) -> dict[str, Any]:
    keys = [*accounts, JUPITER_PERPS_PROGRAM]
    return _legacy_tx(
        signatures=["jupiter-perps-sig"],
        account_keys=keys,
        instructions=[
            {
                "programIdIndex": len(keys) - 1,
                "accounts": list(range(len(accounts))),
                "data": _jupiter_payload(data),
            }
        ],
    )


def _increase_request_data(
    *,
    size_usd_delta: int,
    collateral_token_delta: int,
    side: int = 1,
) -> bytes:
    return (
        _CREATE_INCREASE_POSITION_REQUEST
        + _u64(size_usd_delta)
        + _u64(collateral_token_delta)
        + bytes((side, 0))
        + _opt_u64(75)
        + _opt_u64(None)
        + _opt_u64(None)
        + _opt_bool(None)
        + _u64(9)
    )


def _decrease_request_data(
    *,
    collateral_usd_delta: int,
    size_usd_delta: int,
    entire_position: bool | None = None,
) -> bytes:
    return (
        _CREATE_DECREASE_POSITION_REQUEST
        + _u64(collateral_usd_delta)
        + _u64(size_usd_delta)
        + bytes((0,))
        + _opt_u64(80)
        + _opt_u64(None)
        + _opt_u64(None)
        + _opt_bool(None)
        + _opt_bool(entire_position)
        + _u64(10)
    )


def _request_accounts() -> list[str]:
    return [
        "owner",
        "funding-or-receiving-account",
        "perpetuals",
        "pool",
        "position",
        "position-request",
        "position-request-ata",
        "custody",
        "custody-oracle",
        "collateral-custody",
        "input-or-desired-mint",
        "referral",
        TOKEN_PROGRAM,
        "associated-token-program",
        "system-program",
        "event-authority",
        JUPITER_PERPS_PROGRAM,
    ]


def test_jupiter_perps_hydrator_program_id_schema_and_filters() -> None:
    hydrator = JupiterPerpsHydrator()

    assert hydrator.program_id == JUPITER_PERPS_PROGRAM
    assert hydrator.schema_version == 1
    filters = hydrator.account_filters()
    assert [f.discriminator for f in filters] == [
        JUPITER_PERPS_CUSTODY_DISCRIMINATOR,
        JUPITER_PERPS_POSITION_DISCRIMINATOR,
    ]


def test_jupiter_perps_custody_fixture_parses_to_perp_market_fragment() -> None:
    fragment = JupiterPerpsHydrator().parse_account(
        "JupiterCustodyFixture",
        _custody_fixture(),
    )

    assert fragment.kind == "perp_market"
    assert fragment.protocol_model == JUPITER_PERPS_PROTOCOL_MODEL
    assert fragment.owner is None
    assert fragment.payload["pool"] == _pubkey_str(2)
    assert fragment.payload["mint"] == _pubkey_str(3)
    assert fragment.payload["oracle_references"] == [_pubkey_str(5)]
    assert fragment.payload["oracle"]["oracle_type"] == "pyth"
    assert fragment.payload["pricing"]["max_leverage"] == 250
    assert fragment.payload["assets"]["locked"] == 200_000
    assert fragment.payload["funding_rate_state"] == {
        "cumulative_interest_rate": 123_456_789,
        "last_update": 1_700_000_000,
        "hourly_funding_dbps": 42,
    }
    assert (
        fragment.payload["liquidation_calibration"]
        == "unsupported_without_real_account_oracle_fixtures"
    )


def test_jupiter_perps_position_fixture_parses_to_perp_position_fragment() -> None:
    fragment = JupiterPerpsHydrator().parse_account(
        "JupiterPositionFixture",
        _position_fixture(),
    )

    assert fragment.kind == "perp_position"
    assert fragment.protocol_model == JUPITER_PERPS_PROTOCOL_MODEL
    assert fragment.owner == _pubkey_str(10)
    assert fragment.payload["side"] == "long"
    assert fragment.payload["size_usd"] == 10_000_000
    assert fragment.payload["collateral_usd"] == 2_000_000
    assert fragment.payload["realised_pnl_usd"] == -125_000
    assert fragment.payload["margin_ratio_bps"] == 2_000
    assert fragment.payload["liquidation_state"] == "requires_oracle_and_custody_state"


def test_supported_jupiter_perps_open_request_maps_to_engine_action() -> None:
    tx = _perps_tx(
        data=_increase_request_data(
            size_usd_delta=5_000_000,
            collateral_token_delta=1_000_000,
        ),
        accounts=_request_accounts(),
    )
    snap = SlotSnapshot.from_raw({"slot": 170, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsOpenPositionAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "owner"
    assert action.compute_unit_limit == 55_000
    assert action.token == "custody"
    assert action.collateral == "input-or-desired-mint"
    assert action.size == 5_000_000
    assert action.side is PositionSide.LONG
    assert action.collateral_token_delta == 1_000_000
    assert action.price_slippage == 75
    assert action.counter == 9
    assert action.pool_id == "pool"
    assert action.position_id == "position"
    assert action.request_id == "position-request"
    assert action.oracle_account_ids == frozenset(("custody-oracle",))
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.program_ids == (JUPITER_PERPS_PROGRAM,)


def test_jupiter_perps_collateral_only_decrease_maps_to_adjust_margin() -> None:
    tx = _perps_tx(
        data=_decrease_request_data(
            collateral_usd_delta=400_000,
            size_usd_delta=0,
            entire_position=False,
        ),
        accounts=_request_accounts(),
    )
    snap = SlotSnapshot.from_raw({"slot": 171, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsAdjustMarginAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.direction is MarginDirection.REMOVE
    assert action.amount == 400_000
    assert action.position_id == "position"
    assert action.collateral_custody_id == "collateral-custody"


def test_jupiter_perps_decrease_size_maps_to_close_position() -> None:
    tx = _perps_tx(
        data=_decrease_request_data(
            collateral_usd_delta=100_000,
            size_usd_delta=900_000,
            entire_position=None,
        ),
        accounts=_request_accounts(),
    )
    snap = SlotSnapshot.from_raw({"slot": 172, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsClosePositionAction)
    assert action.size == 900_000
    assert action.collateral_usd_delta == 100_000
    assert action.entire_position is None
    assert action.oracle_account_ids == frozenset(("custody-oracle",))


def test_jupiter_perps_close_request_maps_to_close_position() -> None:
    tx = _perps_tx(
        data=_CLOSE_POSITION_REQUEST,
        accounts=[
            "keeper",
            "owner",
            "owner-ata",
            "pool",
            "position-request",
            "position-request-ata",
            "position",
            TOKEN_PROGRAM,
            "event-authority",
            JUPITER_PERPS_PROGRAM,
        ],
    )
    snap = SlotSnapshot.from_raw({"slot": 173, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsClosePositionAction)
    assert action.agent_id == "owner"
    assert action.entire_position is True
    assert action.position_id == "position"
    assert action.request_id == "position-request"


def test_jupiter_perps_liquidation_maps_to_liquidate_action() -> None:
    tx = _perps_tx(
        data=_LIQUIDATE_FULL_POSITION2 + b"\x01",
        accounts=[
            "liquidator",
            "perpetuals",
            "pool",
            "position",
            "custody",
            "custody-oracle",
            "collateral-custody",
            "collateral-oracle",
            "collateral-token-account",
            "custody-price-update",
            "collateral-price-update",
            "event-authority",
            JUPITER_PERPS_PROGRAM,
        ],
    )
    snap = SlotSnapshot.from_raw({"slot": 174, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsLiquidateAction)
    assert action.agent_id == "liquidator"
    assert action.target_agent_id == "position"
    assert action.position_id == "position"
    assert action.custody_oracle_account == "custody-oracle"
    assert action.collateral_custody_oracle_account == "collateral-oracle"
    assert action.use_price_update is True
    assert action.oracle_account_ids == frozenset(("custody-oracle", "collateral-oracle"))


def test_jupiter_perps_refresh_aum_maps_to_oracle_read_diagnostic() -> None:
    tx = _perps_tx(
        data=_REFRESH_ASSETS_UNDER_MANAGEMENT,
        accounts=["keeper", "perpetuals", "pool"],
    )
    snap = SlotSnapshot.from_raw({"slot": 175, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, JupiterPerpsOracleReadAction)
    assert action.agent_id == "keeper"
    assert action.pool_id == "pool"


def test_unsupported_jupiter_perps_instruction_remains_partial() -> None:
    tx = _perps_tx(data=b"notknown", accounts=["owner", "position"])
    snap = SlotSnapshot.from_raw({"slot": 176, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (JUPITER_PERPS_PROGRAM,)
