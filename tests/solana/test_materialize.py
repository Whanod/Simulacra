"""Tests for src/defi_sim_solana/replay/materialize.py (PRD US-001 #4)."""

from __future__ import annotations

import base64
from hashlib import sha256

from defi_sim.core.types import Action, SwapAction
from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_solana.program_ids import (
    COMPUTE_BUDGET_PROGRAM,
    METEORA_DLMM_PROGRAM,
    RAYDIUM_AMM_V4_PROGRAM,
    SYSTEM_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
    WHIRLPOOL_PROGRAM,
)
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    MaterializedActionMetadata,
    MaterializedSwapAction,
    OpaqueAction,
    PartialDecodedAction,
    TipAction,
    TokenTransferAction,
    action_decode_status,
    decoded_coverage,
    materialize_slot,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot


def _legacy_tx(
    *,
    signatures: list[str],
    account_keys: list[str],
    instructions: list[dict],
) -> dict:
    """getBlock-style legacy tx using ``programIdIndex``."""
    return {
        "transaction": {
            "signatures": signatures,
            "message": {
                "accountKeys": account_keys,
                "instructions": instructions,
            },
        },
        "meta": {"computeUnitsConsumed": 0},
    }


def _parsed_tx(
    *,
    signatures: list[str],
    account_keys: list[str],
    instructions: list[dict],
) -> dict:
    """getBlock-style parsed tx where each instruction has a literal programId."""
    return {
        "transaction": {
            "signatures": signatures,
            "message": {
                "accountKeys": [{"pubkey": k} for k in account_keys],
                "instructions": instructions,
            },
        },
        "meta": {"computeUnitsConsumed": 0},
    }


def _whirlpool_swap_payload(
    *,
    amount: int,
    other_amount_threshold: int,
    amount_specified_is_input: bool,
    a_to_b: bool,
    swap_v2: bool = False,
) -> list[str]:
    discriminator = (
        bytes((43, 4, 237, 11, 26, 201, 30, 98))
        if swap_v2
        else sha256(b"global:swap").digest()[:8]
    )
    payload = (
        discriminator
        + amount.to_bytes(8, "little")
        + other_amount_threshold.to_bytes(8, "little")
        + (4_295_048_016).to_bytes(16, "little")
        + bytes((1 if amount_specified_is_input else 0, 1 if a_to_b else 0))
    )
    if swap_v2:
        payload += b"\x00"
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _dlmm_swap_payload(
    *,
    amount_in: int,
    min_amount_out: int,
    swap2: bool = False,
) -> list[str]:
    discriminator = (
        bytes((65, 75, 63, 76, 235, 91, 91, 136))
        if swap2
        else bytes((248, 198, 158, 145, 225, 117, 135, 200))
    )
    payload = (
        discriminator
        + amount_in.to_bytes(8, "little")
        + min_amount_out.to_bytes(8, "little")
    )
    if swap2:
        payload += (0).to_bytes(4, "little")
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _dlmm_swap_with_price_impact_payload(
    *,
    amount_in: int,
    active_id: int | None,
    max_price_impact_bps: int,
) -> list[str]:
    payload = bytes((56, 173, 230, 208, 173, 228, 156, 205)) + amount_in.to_bytes(
        8, "little"
    )
    if active_id is None:
        payload += b"\x00"
    else:
        payload += b"\x01" + active_id.to_bytes(4, "little", signed=True)
    payload += max_price_impact_bps.to_bytes(2, "little")
    return [base64.b64encode(payload).decode("ascii"), "base64"]


def _raydium_swap_payload(
    *,
    amount_in: int | None = None,
    minimum_amount_out: int | None = None,
    max_amount_in: int | None = None,
    amount_out: int | None = None,
    swap_base_out: bool = False,
) -> list[str]:
    if swap_base_out:
        first_amount = max_amount_in if max_amount_in is not None else 0
        second_amount = amount_out if amount_out is not None else 0
        tag = 11
    else:
        first_amount = amount_in if amount_in is not None else 0
        second_amount = (
            minimum_amount_out if minimum_amount_out is not None else 0
        )
        tag = 9
    payload = (
        bytes((tag,))
        + first_amount.to_bytes(8, "little")
        + second_amount.to_bytes(8, "little")
    )
    return [base64.b64encode(payload).decode("ascii"), "base64"]


# ---------------------------------------------------------------------------
# Empty / minimal slots
# ---------------------------------------------------------------------------


def test_empty_slot_returns_empty_list() -> None:
    snap = SlotSnapshot(slot=160_000_001)
    assert materialize_slot(snap) == []


def test_each_tx_becomes_exactly_one_action() -> None:
    txs = [
        _legacy_tx(
            signatures=[f"sig{i}"],
            account_keys=["payer", "11111111111111111111111111111111"],
            instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
        )
        for i in range(5)
    ]
    snap = SlotSnapshot(
        slot=1,
        transactions=tuple(txs),
        transaction_compute_units=tuple(range(5)),
    )
    actions = materialize_slot(snap)
    assert len(actions) == 5


def test_slot_ordering_is_preserved() -> None:
    txs = [
        _legacy_tx(
            signatures=[f"sig{i}"],
            account_keys=[f"payer{i}", "11111111111111111111111111111111"],
            instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
        )
        for i in range(3)
    ]
    snap = SlotSnapshot(
        slot=1,
        transactions=tuple(txs),
        transaction_compute_units=(0, 0, 0),
    )
    actions = materialize_slot(snap)
    assert [a.agent_id for a in actions] == ["payer0", "payer1", "payer2"]


# ---------------------------------------------------------------------------
# OpaqueAction fallback
# ---------------------------------------------------------------------------


def test_unknown_program_becomes_opaque_action() -> None:
    """PRD line 194 — unknown instructions preserved as opaque records."""
    tx = _legacy_tx(
        signatures=["sigA"],
        account_keys=["fee_payer", "SomeUnknownProgram1111111111111111111111111"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(2_500,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)


def test_opaque_action_carries_compute_units() -> None:
    """Per-tx CU consumption must propagate so gas/CU accounting stays honest."""
    tx = _legacy_tx(
        signatures=["sigA"],
        account_keys=["payer", "Prog"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(42_000,),
    )
    [action] = materialize_slot(snap)
    assert action.compute_unit_limit == 42_000


def test_opaque_action_carries_signature() -> None:
    tx = _legacy_tx(
        signatures=["abc123"],
        account_keys=["payer", "Prog"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.signature == "abc123"


def test_opaque_action_carries_program_ids_and_instruction_count() -> None:
    tx = _legacy_tx(
        signatures=["sig"],
        account_keys=["payer", "ProgA", "ProgB"],
        instructions=[
            {"programIdIndex": 1, "accounts": [0], "data": ""},
            {"programIdIndex": 2, "accounts": [0], "data": ""},
            {"programIdIndex": 1, "accounts": [0], "data": ""},
        ],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.program_ids == ("ProgA", "ProgB", "ProgA")
    assert action.instruction_count == 3
    assert action_decode_status(action) is ActionDecodeStatus.OPAQUE
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.opaque_instruction_count == 3
    assert action.materialized_metadata.unsupported_program_ids == (
        "ProgA",
        "ProgB",
        "ProgA",
    )


def test_fee_payer_becomes_agent_id() -> None:
    tx = _legacy_tx(
        signatures=["sig"],
        account_keys=["the_fee_payer", "ProgA"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert action.agent_id == "the_fee_payer"


def test_opaque_action_carries_slot_and_ordering_metadata() -> None:
    tx = _legacy_tx(
        signatures=["sig"],
        account_keys=["payer", "ProgA"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=42,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.signature == "sig"
    assert action.materialized_metadata.slot == 42
    assert action.materialized_metadata.transaction_index == 0
    assert action.materialized_metadata.instruction_count == 1


def test_malformed_instruction_payload_falls_back_conservatively() -> None:
    tx = _parsed_tx(
        signatures=["sig"],
        account_keys=["payer"],
        instructions=[
            {"programId": 123, "parsed": {"type": "transfer"}},
            {"programIdIndex": "not-an-int", "accounts": [0], "data": ""},
        ],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action_decode_status(action) is ActionDecodeStatus.OPAQUE
    assert action.program_ids == ()
    assert action.instruction_count == 2


# ---------------------------------------------------------------------------
# Wire-shape tolerance
# ---------------------------------------------------------------------------


def test_parsed_instructions_resolve_programId_directly() -> None:
    """Parsed-shape txs already carry ``programId`` as a string."""
    tx = _parsed_tx(
        signatures=["sig"],
        account_keys=["payer"],
        instructions=[
            {"programId": "ProgA", "parsed": {"type": "transfer"}},
            {"programId": "ProgB", "parsed": {}},
        ],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.program_ids == ("ProgA", "ProgB")


def test_legacy_instructions_resolve_via_programIdIndex() -> None:
    tx = _legacy_tx(
        signatures=["sig"],
        account_keys=["payer", "ProgA", "ProgB"],
        instructions=[
            {"programIdIndex": 1, "accounts": [0], "data": ""},
            {"programIdIndex": 2, "accounts": [0], "data": ""},
        ],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.program_ids == ("ProgA", "ProgB")


def test_out_of_range_programIdIndex_is_skipped() -> None:
    """Defensive: malformed indices don't crash, just drop the program id."""
    tx = _legacy_tx(
        signatures=["sig"],
        account_keys=["payer", "ProgA"],
        instructions=[
            {"programIdIndex": 1, "accounts": [0], "data": ""},
            {"programIdIndex": 99, "accounts": [0], "data": ""},
        ],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.program_ids == ("ProgA",)
    assert action.instruction_count == 2


def test_non_dict_transactions_are_skipped() -> None:
    snap = SlotSnapshot(
        slot=1,
        transactions=("just-a-string", None, 42),  # type: ignore[arg-type]
        transaction_compute_units=(0, 0, 0),
    )
    assert materialize_slot(snap) == []


def test_missing_signatures_yields_none() -> None:
    tx = _legacy_tx(
        signatures=[],
        account_keys=["payer", "ProgA"],
        instructions=[{"programIdIndex": 1, "accounts": [0], "data": ""}],
    )
    snap = SlotSnapshot(
        slot=1,
        transactions=(tx,),
        transaction_compute_units=(0,),
    )
    [action] = materialize_slot(snap)
    assert isinstance(action, OpaqueAction)
    assert action.signature is None


# ---------------------------------------------------------------------------
# Token transfer decoding
# ---------------------------------------------------------------------------


def test_parsed_spl_token_transfer_decodes_to_token_transfer_action() -> None:
    tx = _parsed_tx(
        signatures=["token-sig"],
        account_keys=["fee-payer"],
        instructions=[
            {
                "programId": TOKEN_PROGRAM,
                "parsed": {
                    "type": "transfer",
                    "info": {
                        "source": "source-token-account",
                        "destination": "destination-token-account",
                        "authority": "wallet-authority",
                        "amount": "42000000",
                    },
                },
            }
        ],
    )
    snap = SlotSnapshot(
        slot=123,
        transactions=(tx,),
        transaction_compute_units=(4_200,),
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, TokenTransferAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "wallet-authority"
    assert action.source == "source-token-account"
    assert action.destination == "destination-token-account"
    assert action.amount == 42_000_000
    assert action.mint is None
    assert action.authority == "wallet-authority"
    assert action.token_program_id == TOKEN_PROGRAM
    assert action.signature == "token-sig"
    assert action.compute_unit_limit == 4_200
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.slot == 123
    assert action.materialized_metadata.transaction_index == 0
    assert action.materialized_metadata.instruction_index == 0
    assert action.materialized_metadata.decoded_instruction_count == 1


def test_parsed_token_2022_transfer_checked_decodes_to_token_transfer_action() -> None:
    tx = _parsed_tx(
        signatures=["token-2022-sig"],
        account_keys=["fee-payer"],
        instructions=[
            {
                "programId": TOKEN_2022_PROGRAM,
                "parsed": {
                    "type": "transferChecked",
                    "info": {
                        "source": "source-2022-account",
                        "mint": "token-2022-mint",
                        "destination": "destination-2022-account",
                        "authority": "token-2022-authority",
                        "tokenAmount": {
                            "amount": "7",
                            "decimals": 0,
                            "uiAmount": 7,
                            "uiAmountString": "7",
                        },
                    },
                },
            }
        ],
    )
    snap = SlotSnapshot(
        slot=124,
        transactions=(tx,),
        transaction_compute_units=(5_001,),
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, TokenTransferAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "token-2022-authority"
    assert action.source == "source-2022-account"
    assert action.destination == "destination-2022-account"
    assert action.mint == "token-2022-mint"
    assert action.amount == 7
    assert action.token_program_id == TOKEN_2022_PROGRAM
    assert action.signature == "token-2022-sig"
    assert action.compute_unit_limit == 5_001


def test_raw_spl_token_transfer_decodes_from_compiled_instruction_data() -> None:
    amount = 123_456_789
    payload = bytes([3]) + amount.to_bytes(8, "little")
    tx = _legacy_tx(
        signatures=["raw-transfer-sig"],
        account_keys=[
            "fee-payer",
            "source-token-account",
            "destination-token-account",
            "wallet-authority",
            TOKEN_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 4,
                "accounts": [1, 2, 3],
                "data": [base64.b64encode(payload).decode("ascii"), "base64"],
            },
        ],
    )
    snap = SlotSnapshot(
        slot=125, transactions=(tx,), transaction_compute_units=(8_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, TokenTransferAction)
    assert action.source == "source-token-account"
    assert action.destination == "destination-token-account"
    assert action.authority == "wallet-authority"
    assert action.mint is None
    assert action.amount == amount
    assert action.token_program_id == TOKEN_PROGRAM


def test_raw_spl_token_transfer_missing_authority_becomes_partial() -> None:
    payload = bytes([3]) + (123).to_bytes(8, "little")
    tx = _legacy_tx(
        signatures=["raw-transfer-short-sig"],
        account_keys=[
            "fee-payer",
            "source-token-account",
            "destination-token-account",
            TOKEN_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 3,
                "accounts": [1, 2],
                "data": [base64.b64encode(payload).decode("ascii"), "base64"],
            },
        ],
    )
    snap = SlotSnapshot(
        slot=125, transactions=(tx,), transaction_compute_units=(8_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (TOKEN_PROGRAM,)


def test_raw_token_2022_transfer_checked_decodes_mint_account() -> None:
    amount = 99
    decimals = 6
    payload = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
    tx = _legacy_tx(
        signatures=["raw-transfer-checked-sig"],
        account_keys=[
            "fee-payer",
            "source-token-account",
            "token-2022-mint",
            "destination-token-account",
            "wallet-authority",
            TOKEN_2022_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 5,
                "accounts": [1, 2, 3, 4],
                "data": [base64.b64encode(payload).decode("ascii"), "base64"],
            },
        ],
    )
    snap = SlotSnapshot(
        slot=126, transactions=(tx,), transaction_compute_units=(9_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, TokenTransferAction)
    assert action.mint == "token-2022-mint"
    assert action.amount == amount
    assert action.token_program_id == TOKEN_2022_PROGRAM


def test_token_transfer_with_transparent_compute_budget_stays_decoded() -> None:
    tx = _parsed_tx(
        signatures=["budget-token-sig"],
        account_keys=["fee-payer"],
        instructions=[
            {"programId": COMPUTE_BUDGET_PROGRAM, "data": ""},
            {
                "programId": TOKEN_PROGRAM,
                "parsed": {
                    "type": "transfer",
                    "info": {
                        "source": "source-token-account",
                        "destination": "destination-token-account",
                        "authority": "wallet-authority",
                        "amount": "10",
                    },
                },
            },
        ],
    )
    snap = SlotSnapshot(
        slot=127, transactions=(tx,), transaction_compute_units=(3_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, TokenTransferAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.instruction_count == 2
    assert action.materialized_metadata.decoded_instruction_count == 2


def test_unsupported_token_instruction_becomes_partial() -> None:
    tx = _parsed_tx(
        signatures=["approve-sig"],
        account_keys=["fee-payer"],
        instructions=[
            {
                "programId": TOKEN_PROGRAM,
                "parsed": {
                    "type": "approve",
                    "info": {
                        "source": "source-token-account",
                        "delegate": "delegate",
                        "owner": "wallet-authority",
                        "amount": "10",
                    },
                },
            }
        ],
    )
    snap = SlotSnapshot(
        slot=128, transactions=(tx,), transaction_compute_units=(3_500,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.partial_instruction_count == 1
    assert action.unsupported_program_ids == (TOKEN_PROGRAM,)


def test_mixed_token_transfer_and_unknown_instruction_becomes_partial() -> None:
    tx = _parsed_tx(
        signatures=["mixed-sig"],
        account_keys=["fee-payer"],
        instructions=[
            {
                "programId": TOKEN_PROGRAM,
                "parsed": {
                    "type": "transfer",
                    "info": {
                        "source": "source-token-account",
                        "destination": "destination-token-account",
                        "authority": "wallet-authority",
                        "amount": "10",
                    },
                },
            },
            {"programId": "UnknownProgram111111111111111111111111111", "data": ""},
        ],
    )
    snap = SlotSnapshot(
        slot=129, transactions=(tx,), transaction_compute_units=(4_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.decoded_instruction_count == 1
    assert action.opaque_instruction_count == 1
    assert action.decoded_action_types == ("TokenTransferAction",)
    assert action.unsupported_program_ids == (
        "UnknownProgram111111111111111111111111111",
    )


# ---------------------------------------------------------------------------
# Whirlpool swap decoding
# ---------------------------------------------------------------------------


def test_raw_whirlpool_swap_decodes_to_materialized_swap_action() -> None:
    amount_in = 173_768_011
    amount_out = 400_562_437_837
    tx = _legacy_tx(
        signatures=["whirlpool-swap-sig"],
        account_keys=[
            "fee-payer",
            "owner-account-a",
            "whirlpool-pool",
            "vault-a",
            "tick-array-0",
            "tick-array-1",
            "owner-account-b",
            "tick-array-2",
            "vault-b",
            "oracle",
            TOKEN_PROGRAM,
            WHIRLPOOL_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 11,
                "accounts": [10, 0, 2, 1, 3, 6, 8, 4, 5, 7, 9],
                "data": _whirlpool_swap_payload(
                    amount=amount_in,
                    other_amount_threshold=394_652_823_736,
                    amount_specified_is_input=True,
                    a_to_b=True,
                ),
            }
        ],
    )
    tx["meta"] = {
        "computeUnitsConsumed": 86_000,
        "fee": 5_000,
        "preTokenBalances": [
            {"accountIndex": 3, "mint": "mint-a"},
            {"accountIndex": 8, "mint": "mint-b"},
        ],
        "postTokenBalances": [
            {"accountIndex": 3, "mint": "mint-a"},
            {"accountIndex": 8, "mint": "mint-b"},
        ],
        "innerInstructions": [
            {
                "index": 0,
                "instructions": [
                    {
                        "programIdIndex": 10,
                        "accounts": [1, 3, 0],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_in.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                    {
                        "programIdIndex": 10,
                        "accounts": [8, 6, 2],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_out.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                ],
            }
        ],
    }
    snap = SlotSnapshot.from_raw({"slot": 130, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "fee-payer"
    assert action.pool_id == "whirlpool-pool"
    assert action.token_in == "mint-a"
    assert action.token_out == "mint-b"
    assert action.source_token_account == "owner-account-a"
    assert action.destination_token_account == "owner-account-b"
    assert action.amount_in == amount_in
    assert action.amount_out == amount_out
    assert action.protocol_program_id == WHIRLPOOL_PROGRAM
    assert action.compute_unit_limit == 86_000
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.instruction_index == 0
    assert action.materialized_metadata.fee_lamports == 5_000


def test_raw_whirlpool_swap_v2_decodes_mints_from_instruction_accounts() -> None:
    tx = _legacy_tx(
        signatures=["whirlpool-v2-sig"],
        account_keys=[
            "fee-payer",
            TOKEN_PROGRAM,
            TOKEN_2022_PROGRAM,
            "memo-program",
            "token-authority",
            "whirlpool-pool",
            "mint-a",
            "mint-b",
            "owner-account-a",
            "vault-a",
            "owner-account-b",
            "vault-b",
            "tick-array-0",
            "tick-array-1",
            "tick-array-2",
            "oracle",
            WHIRLPOOL_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 16,
                "accounts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                "data": _whirlpool_swap_payload(
                    amount=25_000,
                    other_amount_threshold=99_000,
                    amount_specified_is_input=False,
                    a_to_b=False,
                    swap_v2=True,
                ),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=131, transactions=(tx,), transaction_compute_units=(77_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action.token_in == "mint-b"
    assert action.token_out == "mint-a"
    assert action.source_token_account == "owner-account-b"
    assert action.destination_token_account == "owner-account-a"
    assert action.amount_in == 99_000
    assert action.amount_out == 25_000
    assert action.agent_id == "token-authority"


def test_unsupported_whirlpool_instruction_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["unsupported-whirlpool-sig"],
        account_keys=["fee-payer", WHIRLPOOL_PROGRAM],
        instructions=[
            {
                "programIdIndex": 1,
                "accounts": [0],
                "data": [base64.b64encode(b"not-swap-data").decode("ascii"), "base64"],
            }
        ],
    )
    snap = SlotSnapshot(
        slot=132, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.partial_instruction_count == 1
    assert action.unsupported_program_ids == (WHIRLPOOL_PROGRAM,)


def test_whirlpool_swap_with_short_account_list_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["short-whirlpool-sig"],
        account_keys=[
            "fee-payer",
            "owner-account-a",
            "whirlpool-pool",
            "vault-a",
            "owner-account-b",
            "vault-b",
            TOKEN_PROGRAM,
            WHIRLPOOL_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 7,
                "accounts": [6, 0, 2, 1, 3, 4, 5],
                "data": _whirlpool_swap_payload(
                    amount=1_000,
                    other_amount_threshold=900,
                    amount_specified_is_input=True,
                    a_to_b=True,
                ),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=132, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (WHIRLPOOL_PROGRAM,)


# ---------------------------------------------------------------------------
# Meteora DLMM swap decoding
# ---------------------------------------------------------------------------


def test_raw_dlmm_swap2_decodes_to_materialized_swap_action() -> None:
    amount_in = 5_000_000
    amount_out = 12_345_678
    tx = _legacy_tx(
        signatures=["dlmm-swap-sig"],
        account_keys=[
            "fee-payer",
            "dlmm-lb-pair",
            "bin-array-bitmap-extension",
            "reserve-x",
            "reserve-y",
            "user-token-in",
            "user-token-out",
            "token-x-mint",
            "token-y-mint",
            "oracle",
            "host-fee-in",
            "wallet-authority",
            TOKEN_PROGRAM,
            "event-authority",
            "memo-program",
            METEORA_DLMM_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 15,
                "accounts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12, 14, 13, 15],
                "data": _dlmm_swap_payload(
                    amount_in=amount_in,
                    min_amount_out=12_000_000,
                    swap2=True,
                ),
            }
        ],
    )
    tx["meta"] = {
        "computeUnitsConsumed": 42_727,
        "fee": 5_000,
        "innerInstructions": [
            {
                "index": 0,
                "instructions": [
                    {
                        "programIdIndex": 12,
                        "accounts": [5, 3, 11],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_in.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                    {
                        "programIdIndex": 12,
                        "accounts": [4, 6, 1],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_out.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                ],
            }
        ],
    }
    snap = SlotSnapshot.from_raw({"slot": 133, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "wallet-authority"
    assert action.pool_id == "dlmm-lb-pair"
    assert action.token_in == "token-x-mint"
    assert action.token_out == "token-y-mint"
    assert action.source_token_account == "user-token-in"
    assert action.destination_token_account == "user-token-out"
    assert action.amount_in == amount_in
    assert action.amount_out == amount_out
    assert action.protocol_program_id == METEORA_DLMM_PROGRAM
    assert action.pool_reserve_accounts == ("reserve-x", "reserve-y")
    assert action.bin_array_bitmap_extension == "bin-array-bitmap-extension"
    assert action.compute_unit_limit == 42_727
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.fee_lamports == 5_000


def test_dlmm_swap_with_price_impact_captures_active_bin_id() -> None:
    tx = _legacy_tx(
        signatures=["dlmm-price-impact-sig"],
        account_keys=[
            "fee-payer",
            "dlmm-lb-pair",
            "bin-array-bitmap-extension",
            "reserve-x",
            "reserve-y",
            "user-token-in",
            "user-token-out",
            "token-x-mint",
            "token-y-mint",
            "oracle",
            "host-fee-in",
            "wallet-authority",
            TOKEN_PROGRAM,
            "event-authority",
            METEORA_DLMM_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 14,
                "accounts": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 12, 13, 14],
                "data": _dlmm_swap_with_price_impact_payload(
                    amount_in=99_000,
                    active_id=8_388,
                    max_price_impact_bps=250,
                ),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=134, transactions=(tx,), transaction_compute_units=(70_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action.amount_in == 99_000
    assert action.active_bin_id == 8_388
    assert action.protocol_program_id == METEORA_DLMM_PROGRAM


def test_unsupported_dlmm_instruction_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["unsupported-dlmm-sig"],
        account_keys=["fee-payer", METEORA_DLMM_PROGRAM],
        instructions=[
            {
                "programIdIndex": 1,
                "accounts": [0],
                "data": [base64.b64encode(b"not-swap-data").decode("ascii"), "base64"],
            }
        ],
    )
    snap = SlotSnapshot(
        slot=135, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.partial_instruction_count == 1
    assert action.unsupported_program_ids == (METEORA_DLMM_PROGRAM,)


def test_dlmm_swap_with_short_account_list_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["short-dlmm-sig"],
        account_keys=[
            "fee-payer",
            "dlmm-lb-pair",
            "reserve-x",
            "reserve-y",
            "user-token-in",
            "user-token-out",
            METEORA_DLMM_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 6,
                "accounts": [1, 1, 2, 3, 4, 5],
                "data": _dlmm_swap_payload(
                    amount_in=1_000,
                    min_amount_out=900,
                ),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=135, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (METEORA_DLMM_PROGRAM,)


def test_mixed_whirlpool_and_dlmm_swaps_remain_partial() -> None:
    tx = _legacy_tx(
        signatures=["mixed-whirlpool-dlmm-sig"],
        account_keys=[
            "fee-payer",
            "owner-account-a",
            "whirlpool-pool",
            "vault-a",
            "tick-array-0",
            "tick-array-1",
            "owner-account-b",
            "tick-array-2",
            "vault-b",
            "whirlpool-oracle",
            TOKEN_PROGRAM,
            WHIRLPOOL_PROGRAM,
            "dlmm-lb-pair",
            "bin-array-bitmap-extension",
            "reserve-x",
            "reserve-y",
            "user-token-in",
            "user-token-out",
            "token-x-mint",
            "token-y-mint",
            "dlmm-oracle",
            "host-fee-in",
            "wallet-authority",
            "event-authority",
            METEORA_DLMM_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 11,
                "accounts": [10, 0, 2, 1, 3, 6, 8, 4, 5, 7, 9],
                "data": _whirlpool_swap_payload(
                    amount=1_000,
                    other_amount_threshold=900,
                    amount_specified_is_input=True,
                    a_to_b=True,
                ),
            },
            {
                "programIdIndex": 24,
                "accounts": [
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                    19,
                    20,
                    21,
                    22,
                    10,
                    10,
                    23,
                    24,
                ],
                "data": _dlmm_swap_payload(amount_in=2_000, min_amount_out=1_900),
            },
        ],
    )
    snap = SlotSnapshot(
        slot=136, transactions=(tx,), transaction_compute_units=(120_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.decoded_instruction_count == 2
    assert action.partial_instruction_count == 1
    assert action.program_ids == (WHIRLPOOL_PROGRAM, METEORA_DLMM_PROGRAM)
    assert action.decoded_action_types == (
        "MaterializedSwapAction",
        "MaterializedSwapAction",
    )


# ---------------------------------------------------------------------------
# Raydium AMM v4 swap decoding
# ---------------------------------------------------------------------------


def test_raw_raydium_swap_base_in_decodes_to_materialized_swap_action() -> None:
    amount_in = 1_500_000
    amount_out = 4_250_000
    tx = _legacy_tx(
        signatures=["raydium-swap-in-sig"],
        account_keys=[
            "fee-payer",
            TOKEN_PROGRAM,
            "raydium-amm",
            "amm-authority",
            "amm-open-orders",
            "amm-target-orders",
            "pool-coin-vault",
            "pool-pc-vault",
            "openbook-program",
            "openbook-market",
            "openbook-bids",
            "openbook-asks",
            "openbook-event-queue",
            "market-coin-vault",
            "market-pc-vault",
            "market-vault-signer",
            "user-source-token",
            "user-destination-token",
            "wallet-authority",
            RAYDIUM_AMM_V4_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 19,
                "accounts": [
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                ],
                "data": _raydium_swap_payload(
                    amount_in=amount_in,
                    minimum_amount_out=4_000_000,
                ),
            }
        ],
    )
    tx["meta"] = {
        "computeUnitsConsumed": 64_000,
        "fee": 5_000,
        "preTokenBalances": [
            {"accountIndex": 6, "mint": "coin-mint"},
            {"accountIndex": 7, "mint": "pc-mint"},
        ],
        "postTokenBalances": [
            {"accountIndex": 6, "mint": "coin-mint"},
            {"accountIndex": 7, "mint": "pc-mint"},
        ],
        "innerInstructions": [
            {
                "index": 0,
                "instructions": [
                    {
                        "programIdIndex": 1,
                        "accounts": [16, 6, 18],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_in.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                    {
                        "programIdIndex": 1,
                        "accounts": [7, 17, 3],
                        "data": [
                            base64.b64encode(
                                bytes([3]) + amount_out.to_bytes(8, "little")
                            ).decode("ascii"),
                            "base64",
                        ],
                    },
                ],
            }
        ],
    }
    snap = SlotSnapshot.from_raw({"slot": 137, "transactions": [tx]})

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.agent_id == "wallet-authority"
    assert action.pool_id == "raydium-amm"
    assert action.token_in == "coin-mint"
    assert action.token_out == "pc-mint"
    assert action.source_token_account == "user-source-token"
    assert action.destination_token_account == "user-destination-token"
    assert action.amount_in == amount_in
    assert action.amount_out == amount_out
    assert action.protocol_program_id == RAYDIUM_AMM_V4_PROGRAM
    assert action.pool_reserve_accounts == ("pool-coin-vault", "pool-pc-vault")
    assert action.compute_unit_limit == 64_000
    assert action.materialized_metadata is not None
    assert action.materialized_metadata.fee_lamports == 5_000


def test_raw_raydium_swap_base_out_supports_compact_account_layout() -> None:
    tx = _legacy_tx(
        signatures=["raydium-swap-out-sig"],
        account_keys=[
            "fee-payer",
            TOKEN_PROGRAM,
            "raydium-amm",
            "amm-authority",
            "amm-open-orders",
            "pool-coin-vault",
            "pool-pc-vault",
            "openbook-program",
            "openbook-market",
            "openbook-bids",
            "openbook-asks",
            "openbook-event-queue",
            "market-coin-vault",
            "market-pc-vault",
            "market-vault-signer",
            "user-source-token",
            "user-destination-token",
            "wallet-authority",
            RAYDIUM_AMM_V4_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 18,
                "accounts": [
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                ],
                "data": _raydium_swap_payload(
                    max_amount_in=3_000_000,
                    amount_out=2_750_000,
                    swap_base_out=True,
                ),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=138,
        transactions=(tx,),
        transaction_compute_units=(70_000,),
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, MaterializedSwapAction)
    assert action.protocol_program_id == RAYDIUM_AMM_V4_PROGRAM
    assert action.amount_in == 3_000_000
    assert action.amount_out == 2_750_000
    assert action.pool_reserve_accounts == ("pool-coin-vault", "pool-pc-vault")
    assert action.agent_id == "wallet-authority"


def test_unsupported_raydium_instruction_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["unsupported-raydium-sig"],
        account_keys=["fee-payer", RAYDIUM_AMM_V4_PROGRAM],
        instructions=[
            {
                "programIdIndex": 1,
                "accounts": [0],
                "data": [base64.b64encode(b"not-swap-data").decode("ascii"), "base64"],
            }
        ],
    )
    snap = SlotSnapshot(
        slot=139, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.partial_instruction_count == 1
    assert action.unsupported_program_ids == (RAYDIUM_AMM_V4_PROGRAM,)


def test_raydium_swap_with_missing_owner_account_becomes_partial() -> None:
    tx = _legacy_tx(
        signatures=["missing-owner-raydium-sig"],
        account_keys=[
            "fee-payer",
            TOKEN_PROGRAM,
            "raydium-amm",
            "amm-authority",
            "amm-open-orders",
            "pool-coin-vault",
            "pool-pc-vault",
            "openbook-program",
            "openbook-market",
            "openbook-bids",
            "openbook-asks",
            "openbook-event-queue",
            "market-coin-vault",
            "market-pc-vault",
            "market-vault-signer",
            "user-source-token",
            "user-destination-token",
            RAYDIUM_AMM_V4_PROGRAM,
        ],
        instructions=[
            {
                "programIdIndex": 17,
                "accounts": [
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    999,
                ],
                "data": _raydium_swap_payload(),
            }
        ],
    )
    snap = SlotSnapshot(
        slot=140, transactions=(tx,), transaction_compute_units=(10_000,)
    )

    [action] = materialize_slot(snap)

    assert isinstance(action, PartialDecodedAction)
    assert action_decode_status(action) is ActionDecodeStatus.PARTIAL
    assert action.unsupported_program_ids == (RAYDIUM_AMM_V4_PROGRAM,)


# ---------------------------------------------------------------------------
# decoded_coverage
# ---------------------------------------------------------------------------


def test_decoded_coverage_zero_for_empty_list() -> None:
    coverage = decoded_coverage([])
    assert coverage.total == 0
    assert coverage.decoded_share == 0.0


def test_decoded_coverage_zero_when_all_opaque() -> None:
    actions: list[Action] = [
        OpaqueAction(agent_id="a"),
        OpaqueAction(agent_id="b"),
    ]
    coverage = decoded_coverage(actions)
    assert coverage.decoded == 0
    assert coverage.partial == 0
    assert coverage.opaque == 2
    assert coverage.decoded_share == 0.0


def test_decoded_coverage_one_when_no_opaque() -> None:
    """Anything that isn't an OpaqueAction counts as decoded."""
    actions: list[Action] = [Action(agent_id="a"), Action(agent_id="b")]
    coverage = decoded_coverage(actions)
    assert coverage.decoded == 2
    assert coverage.partial == 0
    assert coverage.opaque == 0
    assert coverage.decoded_share == 1.0


def test_decoded_coverage_mixed() -> None:
    actions: list[Action] = [
        OpaqueAction(agent_id="a"),
        Action(agent_id="b"),
        Action(agent_id="c"),
        OpaqueAction(agent_id="d"),
    ]
    coverage = decoded_coverage(actions)
    assert coverage.decoded == 2
    assert coverage.partial == 0
    assert coverage.opaque == 2
    assert coverage.decoded_share == 0.5


def test_decoded_coverage_distinguishes_decoded_partial_and_opaque() -> None:
    decoded = TokenTransferAction(
        agent_id="wallet",
        source="source-token-account",
        destination="destination-token-account",
        amount=42,
        mint="mint",
        token_program_id="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature="decoded-sig",
            instruction_count=1,
            decoded_instruction_count=1,
        ),
    )
    partial = PartialDecodedAction(
        agent_id="wallet",
        signature="partial-sig",
        program_ids=("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "Unknown"),
        instruction_count=2,
        decoded_instruction_count=1,
        opaque_instruction_count=1,
        decoded_action_types=("TokenTransferAction",),
        unsupported_program_ids=("Unknown",),
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.PARTIAL,
            signature="partial-sig",
            program_ids=("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "Unknown"),
            instruction_count=2,
            decoded_instruction_count=1,
            opaque_instruction_count=1,
            unsupported_program_ids=("Unknown",),
        ),
    )
    opaque = OpaqueAction(
        agent_id="wallet",
        signature="opaque-sig",
        program_ids=("Unknown",),
        instruction_count=1,
    )
    actions: list[Action] = [decoded, partial, opaque]

    assert action_decode_status(decoded) is ActionDecodeStatus.DECODED
    assert action_decode_status(partial) is ActionDecodeStatus.PARTIAL
    assert action_decode_status(opaque) is ActionDecodeStatus.OPAQUE

    coverage = decoded_coverage(actions)
    assert coverage.decoded == 1
    assert coverage.partial == 1
    assert coverage.opaque == 1
    assert coverage.decoded_share == 1 / 3
    assert coverage.partial_share == 1 / 3
    assert coverage.incomplete_share == 2 / 3
    assert coverage.to_dict()["total"] == 3


def test_tip_action_exposes_bundle_tip_metadata_for_counterfactuals() -> None:
    action = TipAction(
        agent_id="searcher",
        recipient="validator-tip-account",
        tip_lamports=10_000,
        bundle_id="bundle-1",
        compute_unit_limit=5_000,
        signature="sig",
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature="sig",
            bundle_id="bundle-1",
            instruction_count=1,
            decoded_instruction_count=1,
        ),
    )
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.tip_lamports == 10_000
    assert action.bundle_id == "bundle-1"
    assert action.compute_unit_limit == 5_000


def test_materialize_decodes_parsed_system_transfer_to_jito_tip_action() -> None:
    snapshot = SlotSnapshot(
        slot=320_000_005,
        transactions=(
            {
                "transaction": {
                    "signatures": ["tip-bundle-sig"],
                    "message": {
                        "accountKeys": [{"pubkey": "searcher"}],
                        "instructions": [
                            {
                                "programId": SYSTEM_PROGRAM,
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "source": "searcher",
                                        "destination": DEFAULT_JITO_TIP_ACCOUNTS[0],
                                        "lamports": 5_000,
                                    },
                                },
                            }
                        ],
                    },
                },
                "meta": {"computeUnitsConsumed": 5_000},
            },
        ),
        transaction_compute_units=(5_000,),
    )

    actions = materialize_slot(snapshot)

    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, TipAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.recipient == DEFAULT_JITO_TIP_ACCOUNTS[0]
    assert action.tip_lamports == 5_000
    assert action.bundle_id == "tip-bundle-sig"


def test_materialized_swap_action_extends_engine_swap_with_pool_metadata() -> None:
    action = MaterializedSwapAction(
        agent_id="trader",
        token_in="USDC",
        token_out="SOL",
        amount_in=1_000_000,
        amount_out=5_000,
        pool_id="pool",
        source_token_account="source",
        destination_token_account="destination",
        protocol_program_id="whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        signature="sig",
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature="sig",
            program_ids=("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",),
            instruction_count=1,
            decoded_instruction_count=1,
        ),
    )
    assert isinstance(action, SwapAction)
    assert action_decode_status(action) is ActionDecodeStatus.DECODED
    assert action.pool_id == "pool"
    assert action.protocol_program_id == "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"


# ---------------------------------------------------------------------------
# Placeholder fixture round-trip
# ---------------------------------------------------------------------------


def test_placeholder_corpus_fixture_round_trips_to_empty_action_list() -> None:
    """The committed placeholder block (160000001) has 0 transactions.

    Materializer must accept it without error and emit zero actions —
    keeps the offline-CI replay path green for the entry-gate slot.
    """
    from defi_sim_solana.replay import get_slot

    snap = get_slot(160_000_001)
    assert materialize_slot(snap) == []
    assert decoded_coverage(materialize_slot(snap)).decoded_share == 0.0
