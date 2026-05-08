"""Materialize raw Solana slot data into engine ``Action`` records (PRD US-001 #4).

``materialize_slot(snapshot)`` walks a :class:`SlotSnapshot` and emits one
``Action`` per transaction in slot order. Decoded protocol actions
(SPL transfers, Whirlpool/Raydium/etc. swaps, lending) become typed engine
actions; everything else falls back to :class:`OpaqueAction` so replay
still has the right transaction count, CU consumption, and ordering — but
is **excluded** from any "matches mainnet" calibration claim. The
mapping table grows alongside Phase 3 protocol models (each gap marked
``# CALIBRATE-3.x``).

``decoded_coverage`` exposes decoded, partially decoded, and opaque counts
so the replay diff API can surface "decoded coverage %" per PRD line 194
without treating mixed transactions as fully modeled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from defi_sim.core.types import (
    Action,
    AdjustMarginAction,
    BorrowAction,
    ClosePositionAction,
    DepositCollateralAction,
    LiquidateAction,
    MarginDirection,
    Numeric,
    OpenPositionAction,
    PositionSide,
    RepayAction,
    SwapAction,
    WithdrawCollateralAction,
)
from defi_sim.engine.bundle_auction import DEFAULT_JITO_TIP_ACCOUNTS
from defi_sim_solana.program_ids import (
    JUPITER_PERPS_PROGRAM,
    KAMINO_LEND_PROGRAM,
    METEORA_DLMM_PROGRAM,
    RAYDIUM_AMM_V4_PROGRAM,
    SYSTEM_PROGRAM,
    TOKEN_PROGRAM_IDS,
    TRANSPARENT_REPLAY_PROGRAM_IDS,
    WHIRLPOOL_PROGRAM,
)

from .slot_client import SlotSnapshot

__all__ = [
    "ActionDecodeStatus",
    "DecodedCoverage",
    "MaterializedActionMetadata",
    "MaterializedSwapAction",
    "KaminoBorrowAction",
    "KaminoDepositAction",
    "KaminoLiquidateAction",
    "KaminoRepayAction",
    "KaminoWithdrawAction",
    "JupiterPerpsAdjustMarginAction",
    "JupiterPerpsClosePositionAction",
    "JupiterPerpsFundingAction",
    "JupiterPerpsLiquidateAction",
    "JupiterPerpsOpenPositionAction",
    "JupiterPerpsOracleReadAction",
    "OpaqueAction",
    "PartialDecodedAction",
    "TipAction",
    "TokenTransferAction",
    "action_decode_status",
    "decoded_coverage",
    "materialize_slot",
]

_WHIRLPOOL_SWAP_DISCRIMINATOR = sha256(b"global:swap").digest()[:8]
_WHIRLPOOL_SWAP_V2_DISCRIMINATOR = bytes((43, 4, 237, 11, 26, 201, 30, 98))
_WHIRLPOOL_SWAP_DATA_LEN = 8 + 8 + 8 + 16 + 1 + 1

_DLMM_SWAP_DISCRIMINATOR = bytes((248, 198, 158, 145, 225, 117, 135, 200))
_DLMM_SWAP2_DISCRIMINATOR = bytes((65, 75, 63, 76, 235, 91, 91, 136))
_DLMM_SWAP_EXACT_OUT_DISCRIMINATOR = bytes((250, 73, 101, 33, 38, 207, 75, 184))
_DLMM_SWAP_EXACT_OUT2_DISCRIMINATOR = bytes((43, 215, 247, 132, 137, 60, 243, 81))
_DLMM_SWAP_WITH_PRICE_IMPACT_DISCRIMINATOR = bytes(
    (56, 173, 230, 208, 173, 228, 156, 205)
)
_DLMM_SWAP_WITH_PRICE_IMPACT2_DISCRIMINATOR = bytes((74, 98, 192, 214, 177, 51, 75, 51))
_DLMM_EXACT_IN_DISCRIMINATORS = frozenset(
    (_DLMM_SWAP_DISCRIMINATOR, _DLMM_SWAP2_DISCRIMINATOR)
)
_DLMM_EXACT_OUT_DISCRIMINATORS = frozenset(
    (_DLMM_SWAP_EXACT_OUT_DISCRIMINATOR, _DLMM_SWAP_EXACT_OUT2_DISCRIMINATOR)
)
_DLMM_PRICE_IMPACT_DISCRIMINATORS = frozenset(
    (
        _DLMM_SWAP_WITH_PRICE_IMPACT_DISCRIMINATOR,
        _DLMM_SWAP_WITH_PRICE_IMPACT2_DISCRIMINATOR,
    )
)
_DLMM_TWO_U64_DATA_LEN = 8 + 8 + 8
_DLMM_PRICE_IMPACT_MIN_DATA_LEN = 8 + 8 + 1 + 2

_RAYDIUM_SWAP_BASE_IN_TAG = 9
_RAYDIUM_SWAP_BASE_OUT_TAG = 11
_RAYDIUM_SWAP_DATA_LEN = 1 + 8 + 8

_KAMINO_DEPOSIT_RESERVE_LIQUIDITY_DISCRIMINATOR = bytes(
    (169, 201, 30, 126, 6, 205, 102, 68)
)
_KAMINO_DEPOSIT_OBLIGATION_COLLATERAL_DISCRIMINATOR = bytes(
    (129, 199, 4, 2, 222, 39, 26, 46)
)
_KAMINO_DEPOSIT_OBLIGATION_COLLATERAL_V2_DISCRIMINATOR = bytes(
    (216, 224, 191, 27, 204, 151, 102, 175)
)
_KAMINO_WITHDRAW_OBLIGATION_COLLATERAL_DISCRIMINATOR = bytes(
    (37, 116, 205, 103, 243, 192, 92, 198)
)
_KAMINO_WITHDRAW_OBLIGATION_COLLATERAL_V2_DISCRIMINATOR = bytes(
    (202, 249, 117, 114, 231, 192, 47, 138)
)
_KAMINO_BORROW_OBLIGATION_LIQUIDITY_DISCRIMINATOR = bytes(
    (121, 127, 18, 204, 73, 245, 225, 65)
)
_KAMINO_BORROW_OBLIGATION_LIQUIDITY_V2_DISCRIMINATOR = bytes(
    (161, 128, 143, 245, 171, 199, 194, 6)
)
_KAMINO_REPAY_OBLIGATION_LIQUIDITY_DISCRIMINATOR = bytes(
    (145, 178, 13, 225, 76, 240, 147, 72)
)
_KAMINO_REPAY_OBLIGATION_LIQUIDITY_V2_DISCRIMINATOR = bytes(
    (116, 174, 213, 76, 180, 53, 210, 144)
)
_KAMINO_LIQUIDATE_OBLIGATION_DISCRIMINATOR = bytes(
    (177, 71, 154, 188, 226, 133, 74, 55)
)
_KAMINO_LIQUIDATE_OBLIGATION_V2_DISCRIMINATOR = bytes(
    (162, 161, 35, 143, 30, 187, 185, 103)
)
_KAMINO_U64_AMOUNT_DATA_LEN = 8 + 8
_KAMINO_LIQUIDATION_DATA_LEN = 8 + 8 + 8 + 8

_JUPITER_CREATE_INCREASE_POSITION_REQUEST_DISCRIMINATOR = bytes(
    (8, 160, 201, 226, 217, 74, 228, 137)
)
_JUPITER_CREATE_INCREASE_POSITION_MARKET_REQUEST_DISCRIMINATOR = bytes(
    (184, 85, 199, 24, 105, 171, 156, 56)
)
_JUPITER_CREATE_DECREASE_POSITION_REQUEST_DISCRIMINATOR = bytes(
    (146, 21, 51, 121, 187, 208, 7, 69)
)
_JUPITER_CREATE_DECREASE_POSITION_MARKET_REQUEST_DISCRIMINATOR = bytes(
    (74, 198, 195, 86, 193, 99, 1, 79)
)
_JUPITER_CLOSE_POSITION_REQUEST_DISCRIMINATOR = bytes(
    (40, 105, 217, 188, 220, 45, 109, 110)
)
_JUPITER_INCREASE_POSITION2_DISCRIMINATOR = bytes(
    (215, 101, 62, 100, 152, 11, 154, 61)
)
_JUPITER_DECREASE_POSITION2_DISCRIMINATOR = bytes(
    (180, 193, 163, 222, 169, 231, 66, 253)
)
_JUPITER_DECREASE_POSITION3_DISCRIMINATOR = bytes(
    (145, 243, 130, 119, 196, 220, 95, 118)
)
_JUPITER_LIQUIDATE_FULL_POSITION2_DISCRIMINATOR = bytes(
    (233, 160, 187, 98, 2, 234, 48, 249)
)
_JUPITER_REFRESH_ASSETS_UNDER_MANAGEMENT_DISCRIMINATOR = bytes(
    (162, 0, 215, 55, 225, 15, 185, 0)
)


# Solana program IDs we recognize at this layer. Decoders for each program
# ship with their ``StateHydrator`` peers in Phase 2.3 (PRD US-003) — the
# constants are surfaced now so ``OpaqueAction.program_ids`` can be matched
# against well-known programs without a string-literal grep.

# Shared action vocabulary:
# - CU and signature counts live on Action.
# - AMM swaps use MaterializedSwapAction, a generic SwapAction subclass that
#   adds Solana pool/account metadata without being Whirlpool/Raydium-specific.
# - Token transfers and direct tips use lightweight actions because the engine
#   has no native "move tokens" or "tip payment" action.
# - Signature, bundle ID, and ordering metadata live in MaterializedActionMetadata
#   so existing engine actions can be annotated without growing chain-specific
#   fields on the core Action class.
# CALIBRATE-3.x: SPL transfer -> TokenTransferAction.
# CALIBRATE-3.x: Whirlpool swap -> MaterializedSwapAction (decoder ships with hydrator).
# CALIBRATE-3.x: Raydium AMM v4 swap -> MaterializedSwapAction (decoder ships with hydrator).
# CALIBRATE-3.x: Meteora DLMM swap -> MaterializedSwapAction (decoder ships with hydrator).
# CALIBRATE-3.x: Kamino / MarginFi lending -> Borrow/Repay/Deposit/Withdraw.
# CALIBRATE-3.x: Jupiter Perps -> Open/Close/AdjustMargin/Liquidate plus
# oracle/funding diagnostics.


class ActionDecodeStatus(str, Enum):
    """Transaction-level decode completeness for replay coverage accounting."""

    DECODED = "decoded"
    PARTIAL = "partial"
    OPAQUE = "opaque"


@dataclass(frozen=True)
class MaterializedActionMetadata:
    """Solana transaction metadata shared by materialized engine actions."""

    decode_status: ActionDecodeStatus
    signature: str | None = None
    slot: int | None = None
    transaction_index: int | None = None
    instruction_index: int | None = None
    program_ids: tuple[str, ...] = ()
    instruction_count: int = 0
    decoded_instruction_count: int = 0
    partial_instruction_count: int = 0
    opaque_instruction_count: int = 0
    unsupported_program_ids: tuple[str, ...] = ()
    fee_lamports: int | None = None
    bundle_id: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.decode_status is ActionDecodeStatus.DECODED


@dataclass(frozen=True)
class DecodedCoverage:
    """Decoded/partial/opaque action counts for a materialized action list."""

    decoded: int = 0
    partial: int = 0
    opaque: int = 0

    @property
    def total(self) -> int:
        return self.decoded + self.partial + self.opaque

    @property
    def decoded_share(self) -> float:
        if not self.total:
            return 0.0
        return self.decoded / self.total

    @property
    def partial_share(self) -> float:
        if not self.total:
            return 0.0
        return self.partial / self.total

    @property
    def opaque_share(self) -> float:
        if not self.total:
            return 0.0
        return self.opaque / self.total

    @property
    def incomplete_share(self) -> float:
        if not self.total:
            return 0.0
        return (self.partial + self.opaque) / self.total

    def to_dict(self) -> dict[str, int | float]:
        return {
            "decoded": self.decoded,
            "partial": self.partial,
            "opaque": self.opaque,
            "total": self.total,
            "decoded_share": self.decoded_share,
            "partial_share": self.partial_share,
            "opaque_share": self.opaque_share,
            "incomplete_share": self.incomplete_share,
        }


@dataclass
class OpaqueAction(Action):
    """An on-chain transaction whose instructions have not been decoded.

    Preserves the right transaction count, CU consumption, and ordering at
    replay time so wall-clock-style metrics stay honest. **Excluded from
    any "matches mainnet" calibration claim** — see PRD line 194 ("decoded
    coverage %"). The decoded version of any specific protocol's traffic
    lands when its hydrator/decoder pair ships in Phase 2.3.
    """

    signature: str | None = None
    program_ids: tuple[str, ...] = ()
    instruction_count: int = 0
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class PartialDecodedAction(Action):
    """A transaction with some known semantics and at least one unknown part.

    Future protocol decoders should use this instead of returning a typed
    action when mixed instructions would make the transaction look fully
    modeled. The record preserves coverage and unsupported-program metadata
    while remaining conservative for calibration eligibility.
    """

    signature: str | None = None
    program_ids: tuple[str, ...] = ()
    instruction_count: int = 0
    decoded_instruction_count: int = 0
    partial_instruction_count: int = 0
    opaque_instruction_count: int = 0
    decoded_action_types: tuple[str, ...] = ()
    unsupported_program_ids: tuple[str, ...] = ()
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class TokenTransferAction(Action):
    """A generic SPL Token / Token-2022 transfer action."""

    source: str = ""
    destination: str = ""
    amount: int = 0
    mint: str | None = None
    authority: str | None = None
    token_program_id: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class TipAction(Action):
    """A direct tip payment represented as an engine action."""

    recipient: str = ""
    tip_lamports: int = 0
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class MaterializedSwapAction(SwapAction):
    """A generic Solana AMM swap action with pool/account metadata."""

    pool_id: str | None = None
    source_token_account: str | None = None
    destination_token_account: str | None = None
    amount_out: Numeric | None = None
    protocol_program_id: str | None = None
    pool_reserve_accounts: tuple[str, ...] = ()
    active_bin_id: int | None = None
    bin_array_bitmap_extension: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class KaminoDepositAction(DepositCollateralAction):
    """Kamino Lend deposit action decoded from a supported instruction."""

    obligation_id: str | None = None
    reserve_id: str | None = None
    lending_market: str | None = None
    source_token_account: str | None = None
    destination_collateral_account: str | None = None
    liquidity_mint: str | None = None
    collateral_mint: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class KaminoWithdrawAction(WithdrawCollateralAction):
    """Kamino Lend withdraw action decoded from a supported instruction."""

    obligation_id: str | None = None
    reserve_id: str | None = None
    lending_market: str | None = None
    source_collateral_account: str | None = None
    destination_token_account: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class KaminoBorrowAction(BorrowAction):
    """Kamino Lend borrow action decoded from a supported instruction."""

    obligation_id: str | None = None
    reserve_id: str | None = None
    lending_market: str | None = None
    liquidity_mint: str | None = None
    reserve_source_liquidity: str | None = None
    destination_token_account: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class KaminoRepayAction(RepayAction):
    """Kamino Lend repay action decoded from a supported instruction."""

    obligation_id: str | None = None
    reserve_id: str | None = None
    lending_market: str | None = None
    liquidity_mint: str | None = None
    source_token_account: str | None = None
    destination_liquidity_account: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class KaminoLiquidateAction(LiquidateAction):
    """Kamino Lend liquidation action decoded from a supported instruction."""

    obligation_id: str | None = None
    lending_market: str | None = None
    repay_reserve_id: str | None = None
    withdraw_reserve_id: str | None = None
    min_acceptable_received_liquidity_amount: int | None = None
    max_allowed_ltv_override_percent: int | None = None
    source_token_account: str | None = None
    destination_collateral_account: str | None = None
    destination_liquidity_account: str | None = None
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsOpenPositionAction(OpenPositionAction):
    """Jupiter Perps open/increase-position request or keeper fulfillment."""

    request_id: str | None = None
    pool_id: str | None = None
    position_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    collateral_token_delta: int | None = None
    request_type: str | None = None
    price_slippage: int | None = None
    jupiter_minimum_out: int | None = None
    trigger_price: int | None = None
    trigger_above_threshold: bool | None = None
    counter: int | None = None
    execution_phase: str = "request"
    funding_update_required: bool = True
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsClosePositionAction(ClosePositionAction):
    """Jupiter Perps close/decrease-position request or keeper fulfillment."""

    request_id: str | None = None
    pool_id: str | None = None
    position_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    collateral_usd_delta: int | None = None
    request_type: str | None = None
    price_slippage: int | None = None
    jupiter_minimum_out: int | None = None
    trigger_price: int | None = None
    trigger_above_threshold: bool | None = None
    entire_position: bool | None = None
    counter: int | None = None
    execution_phase: str = "request"
    funding_update_required: bool = True
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsAdjustMarginAction(AdjustMarginAction):
    """Jupiter Perps collateral-only margin adjustment."""

    request_id: str | None = None
    pool_id: str | None = None
    position_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    request_type: str | None = None
    price_slippage: int | None = None
    jupiter_minimum_out: int | None = None
    trigger_price: int | None = None
    trigger_above_threshold: bool | None = None
    entire_position: bool | None = None
    counter: int | None = None
    execution_phase: str = "request"
    funding_update_required: bool = True
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsFundingAction(Action):
    """Jupiter Perps funding-rate application diagnostic action."""

    pool_id: str | None = None
    position_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    execution_phase: str = "funding_update"
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsOracleReadAction(Action):
    """Jupiter Perps oracle-read diagnostic action."""

    pool_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    custody_oracle_account: str | None = None
    collateral_custody_oracle_account: str | None = None
    use_price_update: bool | None = None
    execution_phase: str = "oracle_read"
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass
class JupiterPerpsLiquidateAction(LiquidateAction):
    """Jupiter Perps full-position liquidation action."""

    pool_id: str | None = None
    position_id: str | None = None
    custody_id: str | None = None
    collateral_custody_id: str | None = None
    custody_oracle_account: str | None = None
    collateral_custody_oracle_account: str | None = None
    use_price_update: bool | None = None
    execution_phase: str = "keeper_fulfillment"
    funding_update_required: bool = True
    signature: str | None = None
    bundle_id: str | None = None
    materialized_metadata: MaterializedActionMetadata | None = None


@dataclass(frozen=True)
class _InnerInstruction:
    parent_index: int
    instruction: dict[str, Any]


@dataclass(frozen=True)
class _WhirlpoolSwapParams:
    variant: str
    amount: int
    other_amount_threshold: int
    sqrt_price_limit: int
    amount_specified_is_input: bool
    a_to_b: bool


@dataclass(frozen=True)
class _WhirlpoolSwapAccounts:
    token_authority: str | None
    whirlpool: str | None
    token_mint_a: str | None
    token_mint_b: str | None
    token_owner_account_a: str | None
    token_vault_a: str | None
    token_owner_account_b: str | None
    token_vault_b: str | None


@dataclass(frozen=True)
class _DlmmSwapParams:
    variant: str
    amount_in: int | None
    min_amount_out: int | None
    max_in_amount: int | None
    out_amount: int | None
    active_bin_id: int | None


@dataclass(frozen=True)
class _DlmmSwapAccounts:
    lb_pair: str | None
    bin_array_bitmap_extension: str | None
    reserve_x: str | None
    reserve_y: str | None
    user_token_in: str | None
    user_token_out: str | None
    token_x_mint: str | None
    token_y_mint: str | None
    oracle: str | None
    user: str | None


@dataclass(frozen=True)
class _RaydiumSwapParams:
    variant: str
    amount_in: int | None
    min_amount_out: int | None
    max_amount_in: int | None
    amount_out: int | None


@dataclass(frozen=True)
class _RaydiumSwapAccounts:
    amm: str | None
    pool_coin_token_account: str | None
    pool_pc_token_account: str | None
    user_source_token_account: str | None
    user_destination_token_account: str | None
    user_source_owner: str | None


def materialize_slot(snapshot: SlotSnapshot) -> list[Action]:
    """Convert a :class:`SlotSnapshot` into engine-formatted actions.

    Emits one action per transaction in the slot's original order. Unknown
    instructions become :class:`OpaqueAction`; per-tx compute units are
    propagated to ``Action.compute_unit_limit`` so downstream gas/CU
    accounting stays consistent with mainnet.
    """
    actions: list[Action] = []
    cu_table = snapshot.transaction_compute_units
    for idx, tx in enumerate(snapshot.transactions):
        if not isinstance(tx, dict):
            continue
        cu = cu_table[idx] if idx < len(cu_table) else None
        action = _materialize_tx(tx, cu, slot=snapshot.slot, transaction_index=idx)
        if action is not None:
            actions.append(action)
    return actions


def action_decode_status(action: Action) -> ActionDecodeStatus:
    """Return the conservative decode status for ``action``.

    Plain engine actions with no materializer metadata predate this vocabulary
    and are treated as fully decoded for backward compatibility. Materialized
    partial and opaque actions are never counted as fully modeled.
    """
    metadata = getattr(action, "materialized_metadata", None)
    if isinstance(metadata, MaterializedActionMetadata):
        return metadata.decode_status
    if isinstance(action, PartialDecodedAction):
        return ActionDecodeStatus.PARTIAL
    if isinstance(action, OpaqueAction):
        return ActionDecodeStatus.OPAQUE
    return ActionDecodeStatus.DECODED


def decoded_coverage(actions: list[Action]) -> DecodedCoverage:
    """Count decoded, partially decoded, and opaque materialized actions.

    Only fully decoded actions contribute to ``DecodedCoverage.decoded_share``.
    Partial actions remain useful for diagnostics but are excluded from any
    "matches mainnet" calibration claim.
    """
    decoded = partial = opaque = 0
    for action in actions:
        status = action_decode_status(action)
        if status is ActionDecodeStatus.DECODED:
            decoded += 1
        elif status is ActionDecodeStatus.PARTIAL:
            partial += 1
        else:
            opaque += 1
    return DecodedCoverage(decoded=decoded, partial=partial, opaque=opaque)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _materialize_tx(
    tx: dict[str, Any],
    compute_units: int | None,
    *,
    slot: int | None,
    transaction_index: int,
) -> Action | None:
    """Convert a single ``getBlock`` transaction entry into an Action."""
    message = _extract_message(tx)
    account_keys = _extract_account_keys(message, tx)
    instructions = _extract_instructions(message)
    inner_instructions = _extract_inner_instructions(tx)
    token_balance_mints = _extract_token_balance_mints(tx, account_keys)
    program_ids = _resolve_program_ids(instructions, account_keys)

    signature = _extract_signature(tx)
    fee_lamports = _extract_fee_lamports(tx)
    fee_payer = account_keys[0] if account_keys else "unknown"

    decoded = _try_decode(
        instructions=instructions,
        inner_instructions=inner_instructions,
        account_keys=account_keys,
        token_balance_mints=token_balance_mints,
        program_ids=program_ids,
        fee_payer=fee_payer,
        compute_units=compute_units,
        fee_lamports=fee_lamports,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
    )
    if decoded is not None:
        return decoded

    metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.OPAQUE,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        program_ids=program_ids,
        instruction_count=len(instructions),
        opaque_instruction_count=len(instructions),
        unsupported_program_ids=program_ids,
        fee_lamports=fee_lamports,
    )
    return OpaqueAction(
        agent_id=fee_payer,
        compute_unit_limit=compute_units,
        signature=signature,
        program_ids=program_ids,
        instruction_count=len(instructions),
        materialized_metadata=metadata,
    )


def _extract_message(tx: dict[str, Any]) -> dict[str, Any]:
    """Locate the message dict inside a getBlock tx entry.

    Handles both the wire shape ``{"transaction": {"message": ...}}`` and
    pre-flattened test shapes that put ``message`` at the top level.
    """
    inner = tx.get("transaction")
    if isinstance(inner, dict):
        msg = inner.get("message")
        if isinstance(msg, dict):
            return msg
    msg = tx.get("message")
    return msg if isinstance(msg, dict) else {}


def _extract_account_keys(
    message: dict[str, Any],
    tx: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    keys = message.get("accountKeys", ())
    out: list[str] = []
    for k in keys:
        if isinstance(k, str):
            out.append(k)
        elif isinstance(k, dict):
            pubkey = k.get("pubkey")
            if isinstance(pubkey, str):
                out.append(pubkey)
    if isinstance(tx, dict):
        meta = tx.get("meta")
        loaded = meta.get("loadedAddresses") if isinstance(meta, dict) else None
        if isinstance(loaded, dict):
            for group in ("writable", "readonly"):
                for pubkey in loaded.get(group) or ():
                    if isinstance(pubkey, str):
                        out.append(pubkey)
    return tuple(out)


def _extract_instructions(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(i for i in message.get("instructions", ()) if isinstance(i, dict))


def _extract_inner_instructions(tx: dict[str, Any]) -> tuple[_InnerInstruction, ...]:
    meta = tx.get("meta")
    groups = meta.get("innerInstructions") if isinstance(meta, dict) else None
    out: list[_InnerInstruction] = []
    for group in groups or ():
        if not isinstance(group, dict):
            continue
        index = group.get("index")
        if not isinstance(index, int):
            continue
        for ix in group.get("instructions") or ():
            if isinstance(ix, dict):
                out.append(_InnerInstruction(parent_index=index, instruction=ix))
    return tuple(out)


def _extract_token_balance_mints(
    tx: dict[str, Any],
    account_keys: tuple[str, ...],
) -> dict[str, str]:
    meta = tx.get("meta")
    if not isinstance(meta, dict):
        return {}
    out: dict[str, str] = {}
    for field in ("preTokenBalances", "postTokenBalances"):
        balances = meta.get(field)
        if not isinstance(balances, list):
            continue
        for balance in balances:
            if not isinstance(balance, dict):
                continue
            account_index = balance.get("accountIndex")
            mint = _read_str(balance.get("mint"))
            if (
                isinstance(account_index, int)
                and 0 <= account_index < len(account_keys)
                and mint is not None
            ):
                out.setdefault(account_keys[account_index], mint)
    return out


def _resolve_program_ids(
    instructions: tuple[dict[str, Any], ...],
    account_keys: tuple[str, ...],
) -> tuple[str, ...]:
    """Extract per-instruction program IDs in order.

    Supports both the parsed shape (``programId`` is a literal string) and
    the legacy/raw shape (``programIdIndex`` indexes ``account_keys``).
    """
    out: list[str] = []
    for ix in instructions:
        program_id = ix.get("programId")
        if isinstance(program_id, str):
            out.append(program_id)
            continue
        idx = ix.get("programIdIndex")
        if isinstance(idx, int) and 0 <= idx < len(account_keys):
            out.append(account_keys[idx])
    return tuple(out)


def _resolve_instruction_program_id(
    ix: dict[str, Any],
    account_keys: tuple[str, ...],
) -> str | None:
    program_id = ix.get("programId")
    if isinstance(program_id, str):
        return program_id
    idx = ix.get("programIdIndex")
    if isinstance(idx, int) and 0 <= idx < len(account_keys):
        return account_keys[idx]
    return None


def _extract_signature(tx: dict[str, Any]) -> str | None:
    inner = tx.get("transaction")
    sigs: Any = ()
    if isinstance(inner, dict):
        sigs = inner.get("signatures") or ()
    if not sigs:
        sigs = tx.get("signatures") or ()
    if not sigs:
        return None
    first = sigs[0]
    return first if isinstance(first, str) else None


def _extract_fee_lamports(tx: dict[str, Any]) -> int | None:
    meta = tx.get("meta")
    fee = meta.get("fee") if isinstance(meta, dict) else None
    if isinstance(fee, int) and fee >= 0:
        return fee
    return None


def _try_decode(
    *,
    instructions: tuple[dict[str, Any], ...],
    inner_instructions: tuple[_InnerInstruction, ...],
    account_keys: tuple[str, ...],
    token_balance_mints: dict[str, str],
    program_ids: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
) -> Action | None:
    """Per-program decoder dispatch.

    SPL Token and Token-2022 transfers are decoded here because they are the
    shared movement primitive for wallet and bundle flows. Whirlpool swaps are
    decoded as the first AMM vertical slice; unsupported Whirlpool variants
    remain partial so the replay surface does not over-claim calibration.
    """
    token_transfers: list[TokenTransferAction] = []
    tips: list[TipAction] = []
    whirlpool_swaps: list[MaterializedSwapAction] = []
    dlmm_swaps: list[MaterializedSwapAction] = []
    raydium_swaps: list[MaterializedSwapAction] = []
    kamino_actions: list[Action] = []
    jupiter_perps_actions: list[Action] = []
    decoded_instruction_count = 0
    partial_instruction_count = 0
    opaque_instruction_count = 0
    unsupported_program_ids: list[str] = []

    for instruction_index, ix in enumerate(instructions):
        program_id = _resolve_instruction_program_id(ix, account_keys)
        if program_id in TRANSPARENT_REPLAY_PROGRAM_IDS:
            decoded_instruction_count += 1
            continue

        if program_id in TOKEN_PROGRAM_IDS:
            transfer = _decode_token_transfer_instruction(
                ix,
                account_keys=account_keys,
                program_id=program_id,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if transfer is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            token_transfers.append(transfer)
            continue

        if program_id == SYSTEM_PROGRAM:
            tip = _decode_system_tip_instruction(
                ix,
                account_keys=account_keys,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if tip is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            tips.append(tip)
            continue

        if program_id == WHIRLPOOL_PROGRAM:
            swap = _decode_whirlpool_swap_instruction(
                ix,
                account_keys=account_keys,
                token_balance_mints=token_balance_mints,
                inner_instructions=inner_instructions,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if swap is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            whirlpool_swaps.append(swap)
            continue

        if program_id == METEORA_DLMM_PROGRAM:
            swap = _decode_dlmm_swap_instruction(
                ix,
                account_keys=account_keys,
                token_balance_mints=token_balance_mints,
                inner_instructions=inner_instructions,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if swap is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            dlmm_swaps.append(swap)
            continue

        if program_id == RAYDIUM_AMM_V4_PROGRAM:
            swap = _decode_raydium_swap_instruction(
                ix,
                account_keys=account_keys,
                token_balance_mints=token_balance_mints,
                inner_instructions=inner_instructions,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if swap is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            raydium_swaps.append(swap)
            continue

        if program_id == KAMINO_LEND_PROGRAM:
            lending_action = _decode_kamino_lending_instruction(
                ix,
                account_keys=account_keys,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if lending_action is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            kamino_actions.append(lending_action)
            continue

        if program_id == JUPITER_PERPS_PROGRAM:
            perps_action = _decode_jupiter_perps_instruction(
                ix,
                account_keys=account_keys,
                fee_payer=fee_payer,
                compute_units=compute_units,
                fee_lamports=fee_lamports,
                signature=signature,
                slot=slot,
                transaction_index=transaction_index,
                instruction_index=instruction_index,
                program_ids=program_ids,
                instruction_count=len(instructions),
            )
            if perps_action is None:
                partial_instruction_count += 1
                unsupported_program_ids.append(program_id)
                continue
            decoded_instruction_count += 1
            jupiter_perps_actions.append(perps_action)
            continue

        if program_id is None:
            opaque_instruction_count += 1
            continue
        unsupported_program_ids.append(program_id)
        opaque_instruction_count += 1

    typed_actions: list[Action] = [
        *token_transfers,
        *tips,
        *whirlpool_swaps,
        *dlmm_swaps,
        *raydium_swaps,
        *kamino_actions,
        *jupiter_perps_actions,
    ]
    if (
        len(typed_actions) == 1
        and not partial_instruction_count
        and not opaque_instruction_count
    ):
        action = typed_actions[0]
        action.materialized_metadata = MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            signature=signature,
            slot=slot,
            transaction_index=transaction_index,
            instruction_index=action.materialized_metadata.instruction_index
            if action.materialized_metadata
            else None,
            program_ids=program_ids,
            instruction_count=len(instructions),
            decoded_instruction_count=decoded_instruction_count,
            fee_lamports=fee_lamports,
        )
        return action

    if typed_actions or partial_instruction_count:
        if len(typed_actions) > 1 and not partial_instruction_count:
            partial_instruction_count = 1
        unsupported = tuple(unsupported_program_ids)
        metadata = MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.PARTIAL,
            signature=signature,
            slot=slot,
            transaction_index=transaction_index,
            program_ids=program_ids,
            instruction_count=len(instructions),
            decoded_instruction_count=decoded_instruction_count,
            partial_instruction_count=partial_instruction_count,
            opaque_instruction_count=opaque_instruction_count,
            unsupported_program_ids=unsupported,
            fee_lamports=fee_lamports,
        )
        return PartialDecodedAction(
            agent_id=typed_actions[0].agent_id if typed_actions else fee_payer,
            compute_unit_limit=compute_units,
            signature=signature,
            program_ids=program_ids,
            instruction_count=len(instructions),
            decoded_instruction_count=decoded_instruction_count,
            partial_instruction_count=partial_instruction_count,
            opaque_instruction_count=opaque_instruction_count,
            decoded_action_types=tuple(
                type(action).__name__ for action in typed_actions
            ),
            unsupported_program_ids=unsupported,
            materialized_metadata=metadata,
        )

    return None


def _decode_token_transfer_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    program_id: str,
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> TokenTransferAction | None:
    parsed = ix.get("parsed")
    if isinstance(parsed, dict):
        transfer = _decode_parsed_token_transfer(
            parsed,
            program_id=program_id,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
        )
    else:
        transfer = _decode_raw_token_transfer(
            ix,
            account_keys=account_keys,
            program_id=program_id,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
        )
    if transfer is None:
        return None
    transfer.materialized_metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )
    return transfer


def _decode_system_tip_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> TipAction | None:
    parsed = ix.get("parsed")
    if isinstance(parsed, dict):
        tip = _decode_parsed_system_tip(
            parsed,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
        )
    else:
        tip = _decode_raw_system_tip(
            ix,
            account_keys=account_keys,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
        )
    if tip is None:
        return None
    tip.materialized_metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
        bundle_id=tip.bundle_id,
    )
    return tip


def _decode_parsed_system_tip(
    parsed: dict[str, Any],
    *,
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
) -> TipAction | None:
    instruction_type = parsed.get("type")
    if not isinstance(instruction_type, str) or instruction_type.lower() != "transfer":
        return None
    info = parsed.get("info")
    if not isinstance(info, dict):
        return None
    source = _read_str(info.get("source")) or fee_payer
    destination = _read_str(info.get("destination"))
    lamports = _parsed_lamports(info.get("lamports"))
    if destination not in DEFAULT_JITO_TIP_ACCOUNTS or lamports is None:
        return None
    return TipAction(
        agent_id=source,
        compute_unit_limit=compute_units,
        recipient=destination,
        tip_lamports=lamports,
        signature=signature,
        bundle_id=signature,
    )


def _decode_raw_system_tip(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
) -> TipAction | None:
    data = _decode_instruction_data(ix.get("data"))
    if len(data) < 12:
        return None
    tag = int.from_bytes(data[:4], "little")
    if tag != 2:
        return None
    source = _instruction_account(ix, account_keys, 0) or fee_payer
    destination = _instruction_account(ix, account_keys, 1)
    lamports = int.from_bytes(data[4:12], "little")
    if destination not in DEFAULT_JITO_TIP_ACCOUNTS:
        return None
    return TipAction(
        agent_id=source,
        compute_unit_limit=compute_units,
        recipient=destination,
        tip_lamports=lamports,
        signature=signature,
        bundle_id=signature,
    )


def _decode_parsed_token_transfer(
    parsed: dict[str, Any],
    *,
    program_id: str,
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
) -> TokenTransferAction | None:
    instruction_type = parsed.get("type")
    if not isinstance(instruction_type, str):
        return None
    normalized = instruction_type.lower()
    if normalized not in {"transfer", "transferchecked", "transfercheckedwithfee"}:
        return None
    info = parsed.get("info")
    if not isinstance(info, dict):
        return None
    source = _read_str(info.get("source"))
    destination = _read_str(info.get("destination"))
    authority = _read_str(info.get("authority")) or _read_str(info.get("owner"))
    mint = _read_str(info.get("mint"))
    amount = _parsed_token_amount(info)
    if source is None or destination is None or amount is None:
        return None
    return TokenTransferAction(
        agent_id=authority or fee_payer,
        compute_unit_limit=compute_units,
        source=source,
        destination=destination,
        amount=amount,
        mint=mint,
        authority=authority,
        token_program_id=program_id,
        signature=signature,
    )


def _decode_raw_token_transfer(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    program_id: str,
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
) -> TokenTransferAction | None:
    data = _decode_instruction_data(ix.get("data"))
    if len(data) < 9:
        return None
    tag = data[0]
    account_count = _instruction_account_count(ix)
    if tag == 3:
        if account_count < 3:
            return None
        source = _instruction_account(ix, account_keys, 0)
        destination = _instruction_account(ix, account_keys, 1)
        authority = _instruction_account(ix, account_keys, 2)
        mint = None
        amount = int.from_bytes(data[1:9], "little")
    elif tag == 12 and len(data) >= 10:
        if account_count < 4:
            return None
        source = _instruction_account(ix, account_keys, 0)
        mint = _instruction_account(ix, account_keys, 1)
        destination = _instruction_account(ix, account_keys, 2)
        authority = _instruction_account(ix, account_keys, 3)
        amount = int.from_bytes(data[1:9], "little")
    else:
        return None
    if source is None or destination is None or authority is None:
        return None
    if tag == 12 and mint is None:
        return None
    return TokenTransferAction(
        agent_id=authority or fee_payer,
        compute_unit_limit=compute_units,
        source=source,
        destination=destination,
        amount=amount,
        mint=mint,
        authority=authority,
        token_program_id=program_id,
        signature=signature,
    )


def _decode_kamino_lending_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> Action | None:
    data = _decode_instruction_data(ix.get("data"))
    discriminator = data[:8]

    metadata = _kamino_metadata(
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        fee_lamports=fee_lamports,
    )

    if discriminator == _KAMINO_DEPOSIT_RESERVE_LIQUIDITY_DISCRIMINATOR:
        amount = _kamino_u64_amount(data)
        if amount is None:
            return None
        return KaminoDepositAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 4) or "",
            amount=amount,
            reserve_id=_instruction_account(ix, account_keys, 1),
            lending_market=_instruction_account(ix, account_keys, 2),
            source_token_account=_instruction_account(ix, account_keys, 7),
            destination_collateral_account=_instruction_account(
                ix,
                account_keys,
                8,
            ),
            liquidity_mint=_instruction_account(ix, account_keys, 4),
            collateral_mint=_instruction_account(ix, account_keys, 6),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _KAMINO_DEPOSIT_OBLIGATION_COLLATERAL_DISCRIMINATOR,
        _KAMINO_DEPOSIT_OBLIGATION_COLLATERAL_V2_DISCRIMINATOR,
    }:
        amount = _kamino_u64_amount(data)
        if amount is None:
            return None
        return KaminoDepositAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 5) or "",
            amount=amount,
            obligation_id=_instruction_account(ix, account_keys, 1),
            reserve_id=_instruction_account(ix, account_keys, 4),
            lending_market=_instruction_account(ix, account_keys, 2),
            source_token_account=_instruction_account(ix, account_keys, 9),
            destination_collateral_account=_instruction_account(
                ix,
                account_keys,
                8,
            ),
            liquidity_mint=_instruction_account(ix, account_keys, 5),
            collateral_mint=_instruction_account(ix, account_keys, 7),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _KAMINO_WITHDRAW_OBLIGATION_COLLATERAL_DISCRIMINATOR,
        _KAMINO_WITHDRAW_OBLIGATION_COLLATERAL_V2_DISCRIMINATOR,
    }:
        amount = _kamino_u64_amount(data)
        if amount is None:
            return None
        return KaminoWithdrawAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 4) or "",
            amount=amount,
            obligation_id=_instruction_account(ix, account_keys, 1),
            reserve_id=_instruction_account(ix, account_keys, 4),
            lending_market=_instruction_account(ix, account_keys, 2),
            source_collateral_account=_instruction_account(ix, account_keys, 5),
            destination_token_account=_instruction_account(ix, account_keys, 6),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _KAMINO_BORROW_OBLIGATION_LIQUIDITY_DISCRIMINATOR,
        _KAMINO_BORROW_OBLIGATION_LIQUIDITY_V2_DISCRIMINATOR,
    }:
        amount = _kamino_u64_amount(data)
        if amount is None:
            return None
        return KaminoBorrowAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 5) or "",
            amount=amount,
            obligation_id=_instruction_account(ix, account_keys, 1),
            reserve_id=_instruction_account(ix, account_keys, 4),
            lending_market=_instruction_account(ix, account_keys, 2),
            liquidity_mint=_instruction_account(ix, account_keys, 5),
            reserve_source_liquidity=_instruction_account(ix, account_keys, 6),
            destination_token_account=_instruction_account(ix, account_keys, 8),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _KAMINO_REPAY_OBLIGATION_LIQUIDITY_DISCRIMINATOR,
        _KAMINO_REPAY_OBLIGATION_LIQUIDITY_V2_DISCRIMINATOR,
    }:
        amount = _kamino_u64_amount(data)
        if amount is None:
            return None
        return KaminoRepayAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 4) or "",
            amount=amount,
            obligation_id=_instruction_account(ix, account_keys, 1),
            reserve_id=_instruction_account(ix, account_keys, 3),
            lending_market=_instruction_account(ix, account_keys, 2),
            liquidity_mint=_instruction_account(ix, account_keys, 4),
            destination_liquidity_account=_instruction_account(
                ix,
                account_keys,
                5,
            ),
            source_token_account=_instruction_account(ix, account_keys, 6),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _KAMINO_LIQUIDATE_OBLIGATION_DISCRIMINATOR,
        _KAMINO_LIQUIDATE_OBLIGATION_V2_DISCRIMINATOR,
    }:
        params = _kamino_liquidation_params(data)
        if params is None:
            return None
        amount, min_received, max_ltv_override = params
        return KaminoLiquidateAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            target_agent_id=_instruction_account(ix, account_keys, 1) or "",
            repay_token=_instruction_account(ix, account_keys, 5) or "",
            repay_amount=amount,
            seize_token=_instruction_account(ix, account_keys, 9) or "",
            obligation_id=_instruction_account(ix, account_keys, 1),
            lending_market=_instruction_account(ix, account_keys, 2),
            repay_reserve_id=_instruction_account(ix, account_keys, 4),
            withdraw_reserve_id=_instruction_account(ix, account_keys, 7),
            min_acceptable_received_liquidity_amount=min_received,
            max_allowed_ltv_override_percent=max_ltv_override,
            source_token_account=_instruction_account(ix, account_keys, 13),
            destination_collateral_account=_instruction_account(
                ix,
                account_keys,
                14,
            ),
            destination_liquidity_account=_instruction_account(
                ix,
                account_keys,
                15,
            ),
            signature=signature,
            materialized_metadata=metadata,
        )

    return None


def _kamino_metadata(
    *,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
    fee_lamports: int | None,
) -> MaterializedActionMetadata:
    return MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )


def _kamino_u64_amount(data: bytes) -> int | None:
    if len(data) < _KAMINO_U64_AMOUNT_DATA_LEN:
        return None
    return int.from_bytes(data[8:16], "little", signed=False)


def _kamino_liquidation_params(data: bytes) -> tuple[int, int, int] | None:
    if len(data) < _KAMINO_LIQUIDATION_DATA_LEN:
        return None
    return (
        int.from_bytes(data[8:16], "little", signed=False),
        int.from_bytes(data[16:24], "little", signed=False),
        int.from_bytes(data[24:32], "little", signed=False),
    )


def _decode_jupiter_perps_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> Action | None:
    data = _decode_instruction_data(ix.get("data"))
    discriminator = data[:8]

    metadata = _jupiter_perps_metadata(
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        fee_lamports=fee_lamports,
    )

    if discriminator == _JUPITER_CREATE_INCREASE_POSITION_REQUEST_DISCRIMINATOR:
        params = _jupiter_increase_request_params(data, market_request=False)
        if params is None:
            return None
        return _jupiter_open_or_add_margin_action(
            ix,
            account_keys=account_keys,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
            metadata=metadata,
            params=params,
            market_request=False,
        )

    if discriminator == _JUPITER_CREATE_INCREASE_POSITION_MARKET_REQUEST_DISCRIMINATOR:
        params = _jupiter_increase_request_params(data, market_request=True)
        if params is None:
            return None
        return _jupiter_open_or_add_margin_action(
            ix,
            account_keys=account_keys,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
            metadata=metadata,
            params=params,
            market_request=True,
        )

    if discriminator == _JUPITER_CREATE_DECREASE_POSITION_REQUEST_DISCRIMINATOR:
        params = _jupiter_decrease_request_params(data, market_request=False)
        if params is None:
            return None
        return _jupiter_close_or_remove_margin_action(
            ix,
            account_keys=account_keys,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
            metadata=metadata,
            params=params,
            market_request=False,
        )

    if discriminator == _JUPITER_CREATE_DECREASE_POSITION_MARKET_REQUEST_DISCRIMINATOR:
        params = _jupiter_decrease_request_params(data, market_request=True)
        if params is None:
            return None
        return _jupiter_close_or_remove_margin_action(
            ix,
            account_keys=account_keys,
            fee_payer=fee_payer,
            compute_units=compute_units,
            signature=signature,
            metadata=metadata,
            params=params,
            market_request=True,
        )

    if discriminator == _JUPITER_CLOSE_POSITION_REQUEST_DISCRIMINATOR:
        if len(data) != 8:
            return None
        return JupiterPerpsClosePositionAction(
            agent_id=_instruction_account(ix, account_keys, 1) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 6) or "",
            size=None,
            pool_id=_instruction_account(ix, account_keys, 3),
            position_id=_instruction_account(ix, account_keys, 6),
            request_id=_instruction_account(ix, account_keys, 4),
            entire_position=True,
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator == _JUPITER_INCREASE_POSITION2_DISCRIMINATOR:
        use_price_update = _jupiter_bool_param(data)
        if use_price_update is None:
            return None
        return JupiterPerpsOpenPositionAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, 6) or "",
            collateral=_instruction_account(ix, account_keys, 8) or "",
            size=0,
            side=PositionSide.LONG,
            leverage=1,
            pool_id=_instruction_account(ix, account_keys, 2),
            position_id=_instruction_account(ix, account_keys, 5),
            request_id=_instruction_account(ix, account_keys, 3),
            custody_id=_instruction_account(ix, account_keys, 6),
            collateral_custody_id=_instruction_account(ix, account_keys, 8),
            execution_phase="keeper_fulfillment",
            oracle_account_ids=frozenset(
                _non_none_accounts(
                    _instruction_account(ix, account_keys, 7),
                    _instruction_account(ix, account_keys, 9),
                )
            ),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator in {
        _JUPITER_DECREASE_POSITION2_DISCRIMINATOR,
        _JUPITER_DECREASE_POSITION3_DISCRIMINATOR,
    }:
        use_price_update = _jupiter_bool_param(data)
        if use_price_update is None:
            return None
        if discriminator == _JUPITER_DECREASE_POSITION2_DISCRIMINATOR:
            owner_idx = 2
            pool_idx = 5
            request_idx = 6
            position_idx = 8
            custody_idx = 9
            oracle_idx = 10
            collateral_idx = 11
            collateral_oracle_idx = 12
        else:
            owner_idx = 1
            pool_idx = 4
            request_idx = 5
            position_idx = 7
            custody_idx = 8
            oracle_idx = 9
            collateral_idx = 10
            collateral_oracle_idx = 11
        return JupiterPerpsClosePositionAction(
            agent_id=_instruction_account(ix, account_keys, owner_idx) or fee_payer,
            compute_unit_limit=compute_units,
            token=_instruction_account(ix, account_keys, position_idx) or "",
            size=None,
            pool_id=_instruction_account(ix, account_keys, pool_idx),
            position_id=_instruction_account(ix, account_keys, position_idx),
            request_id=_instruction_account(ix, account_keys, request_idx),
            custody_id=_instruction_account(ix, account_keys, custody_idx),
            collateral_custody_id=_instruction_account(
                ix,
                account_keys,
                collateral_idx,
            ),
            execution_phase="keeper_fulfillment",
            oracle_account_ids=frozenset(
                _non_none_accounts(
                    _instruction_account(ix, account_keys, oracle_idx),
                    _instruction_account(ix, account_keys, collateral_oracle_idx),
                )
            ),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator == _JUPITER_LIQUIDATE_FULL_POSITION2_DISCRIMINATOR:
        use_price_update = _jupiter_bool_param(data)
        if use_price_update is None:
            return None
        return JupiterPerpsLiquidateAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            target_agent_id=_instruction_account(ix, account_keys, 3) or "",
            repay_token=_instruction_account(ix, account_keys, 4) or "",
            repay_amount=0,
            seize_token=_instruction_account(ix, account_keys, 6) or "",
            pool_id=_instruction_account(ix, account_keys, 2),
            position_id=_instruction_account(ix, account_keys, 3),
            custody_id=_instruction_account(ix, account_keys, 4),
            collateral_custody_id=_instruction_account(ix, account_keys, 6),
            custody_oracle_account=_instruction_account(ix, account_keys, 5),
            collateral_custody_oracle_account=_instruction_account(
                ix,
                account_keys,
                7,
            ),
            use_price_update=use_price_update,
            oracle_account_ids=frozenset(
                _non_none_accounts(
                    _instruction_account(ix, account_keys, 5),
                    _instruction_account(ix, account_keys, 7),
                )
            ),
            signature=signature,
            materialized_metadata=metadata,
        )

    if discriminator == _JUPITER_REFRESH_ASSETS_UNDER_MANAGEMENT_DISCRIMINATOR:
        if len(data) != 8:
            return None
        return JupiterPerpsOracleReadAction(
            agent_id=_instruction_account(ix, account_keys, 0) or fee_payer,
            compute_unit_limit=compute_units,
            pool_id=_instruction_account(ix, account_keys, 2),
            signature=signature,
            materialized_metadata=metadata,
        )

    return None


def _jupiter_open_or_add_margin_action(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
    metadata: MaterializedActionMetadata,
    params: dict[str, int | bool | str | None],
    market_request: bool,
) -> Action | None:
    owner = _instruction_account(ix, account_keys, 0) or fee_payer
    pool = _instruction_account(ix, account_keys, 3)
    position = _instruction_account(ix, account_keys, 4)
    request = _instruction_account(ix, account_keys, 5)
    custody = _instruction_account(ix, account_keys, 7)
    collateral_custody = _instruction_account(ix, account_keys, 8 if market_request else 9)
    collateral_mint = _instruction_account(ix, account_keys, 9 if market_request else 10)
    oracle_accounts = ()
    if not market_request:
        oracle_accounts = _non_none_accounts(_instruction_account(ix, account_keys, 8))

    size_usd_delta = int(params["size_usd_delta"] or 0)
    collateral_delta = int(params["collateral_token_delta"] or 0)
    side = _jupiter_position_side(params.get("side"))
    if side is None:
        return None
    if size_usd_delta == 0 and collateral_delta > 0:
        return JupiterPerpsAdjustMarginAction(
            agent_id=owner,
            compute_unit_limit=compute_units,
            token=collateral_mint or collateral_custody or "",
            collateral=collateral_mint or collateral_custody or "",
            amount=collateral_delta,
            direction=MarginDirection.ADD,
            pool_id=pool,
            position_id=position,
            request_id=request,
            custody_id=custody,
            collateral_custody_id=collateral_custody,
            request_type=_read_str(params.get("request_type")),
            price_slippage=_read_optional_int(params.get("price_slippage")),
            jupiter_minimum_out=_read_optional_int(params.get("jupiter_minimum_out")),
            trigger_price=_read_optional_int(params.get("trigger_price")),
            trigger_above_threshold=_read_optional_bool(
                params.get("trigger_above_threshold")
            ),
            counter=_read_optional_int(params.get("counter")),
            oracle_account_ids=frozenset(oracle_accounts),
            signature=signature,
            materialized_metadata=metadata,
        )
    return JupiterPerpsOpenPositionAction(
        agent_id=owner,
        compute_unit_limit=compute_units,
        token=custody or "",
        collateral=collateral_mint or collateral_custody or "",
        size=size_usd_delta,
        side=side,
        leverage=_jupiter_leverage(size_usd_delta, collateral_delta),
        pool_id=pool,
        position_id=position,
        request_id=request,
        custody_id=custody,
        collateral_custody_id=collateral_custody,
        collateral_token_delta=collateral_delta,
        request_type=_read_str(params.get("request_type")),
        price_slippage=_read_optional_int(params.get("price_slippage")),
        jupiter_minimum_out=_read_optional_int(params.get("jupiter_minimum_out")),
        trigger_price=_read_optional_int(params.get("trigger_price")),
        trigger_above_threshold=_read_optional_bool(
            params.get("trigger_above_threshold")
        ),
        counter=_read_optional_int(params.get("counter")),
        oracle_account_ids=frozenset(oracle_accounts),
        signature=signature,
        materialized_metadata=metadata,
    )


def _jupiter_close_or_remove_margin_action(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    fee_payer: str,
    compute_units: int | None,
    signature: str | None,
    metadata: MaterializedActionMetadata,
    params: dict[str, int | bool | str | None],
    market_request: bool,
) -> Action | None:
    owner = _instruction_account(ix, account_keys, 0) or fee_payer
    pool = _instruction_account(ix, account_keys, 3)
    position = _instruction_account(ix, account_keys, 4)
    request = _instruction_account(ix, account_keys, 5)
    custody = _instruction_account(ix, account_keys, 7)
    collateral_custody = _instruction_account(ix, account_keys, 8 if market_request else 9)
    desired_mint = _instruction_account(ix, account_keys, 9 if market_request else 10)
    oracle_accounts = ()
    if not market_request:
        oracle_accounts = _non_none_accounts(_instruction_account(ix, account_keys, 8))

    size_usd_delta = int(params["size_usd_delta"] or 0)
    collateral_delta = int(params["collateral_usd_delta"] or 0)
    entire_position = _read_optional_bool(params.get("entire_position"))
    if size_usd_delta == 0 and collateral_delta > 0 and not entire_position:
        return JupiterPerpsAdjustMarginAction(
            agent_id=owner,
            compute_unit_limit=compute_units,
            token=desired_mint or collateral_custody or "",
            collateral=desired_mint or collateral_custody or "",
            amount=collateral_delta,
            direction=MarginDirection.REMOVE,
            pool_id=pool,
            position_id=position,
            request_id=request,
            custody_id=custody,
            collateral_custody_id=collateral_custody,
            request_type=_read_str(params.get("request_type")),
            price_slippage=_read_optional_int(params.get("price_slippage")),
            jupiter_minimum_out=_read_optional_int(params.get("jupiter_minimum_out")),
            trigger_price=_read_optional_int(params.get("trigger_price")),
            trigger_above_threshold=_read_optional_bool(
                params.get("trigger_above_threshold")
            ),
            entire_position=entire_position,
            counter=_read_optional_int(params.get("counter")),
            oracle_account_ids=frozenset(oracle_accounts),
            signature=signature,
            materialized_metadata=metadata,
        )
    return JupiterPerpsClosePositionAction(
        agent_id=owner,
        compute_unit_limit=compute_units,
        token=position or custody or "",
        size=None if entire_position else size_usd_delta,
        pool_id=pool,
        position_id=position,
        request_id=request,
        custody_id=custody,
        collateral_custody_id=collateral_custody,
        collateral_usd_delta=collateral_delta,
        request_type=_read_str(params.get("request_type")),
        price_slippage=_read_optional_int(params.get("price_slippage")),
        jupiter_minimum_out=_read_optional_int(params.get("jupiter_minimum_out")),
        trigger_price=_read_optional_int(params.get("trigger_price")),
        trigger_above_threshold=_read_optional_bool(
            params.get("trigger_above_threshold")
        ),
        entire_position=entire_position,
        counter=_read_optional_int(params.get("counter")),
        oracle_account_ids=frozenset(oracle_accounts),
        signature=signature,
        materialized_metadata=metadata,
    )


def _jupiter_perps_metadata(
    *,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
    fee_lamports: int | None,
) -> MaterializedActionMetadata:
    return MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )


def _jupiter_increase_request_params(
    data: bytes,
    *,
    market_request: bool,
) -> dict[str, int | bool | str | None] | None:
    offset = 8
    size_usd_delta = _read_u64_at(data, offset)
    collateral_token_delta = _read_u64_at(data, offset + 8)
    side = _read_u8_at(data, offset + 16)
    if size_usd_delta is None or collateral_token_delta is None or side is None:
        return None
    offset += 17
    if market_request:
        request_type = "market"
        price_slippage = _read_u64_at(data, offset)
        if price_slippage is None:
            return None
        offset += 8
    else:
        request_type_raw = _read_u8_at(data, offset)
        if request_type_raw is None:
            return None
        request_type = _jupiter_request_type_name(request_type_raw)
        offset += 1
        option = _read_option_u64(data, offset)
        if option is None:
            return None
        price_slippage, offset = option
    option = _read_option_u64(data, offset)
    if option is None:
        return None
    jupiter_minimum_out, offset = option
    if market_request:
        trigger_price = None
        trigger_above_threshold = None
    else:
        option = _read_option_u64(data, offset)
        if option is None:
            return None
        trigger_price, offset = option
        bool_option = _read_option_bool(data, offset)
        if bool_option is None:
            return None
        trigger_above_threshold, offset = bool_option
    counter = _read_u64_at(data, offset)
    if counter is None:
        return None
    return {
        "size_usd_delta": size_usd_delta,
        "collateral_token_delta": collateral_token_delta,
        "side": side,
        "request_type": request_type,
        "price_slippage": price_slippage,
        "jupiter_minimum_out": jupiter_minimum_out,
        "trigger_price": trigger_price,
        "trigger_above_threshold": trigger_above_threshold,
        "counter": counter,
    }


def _jupiter_decrease_request_params(
    data: bytes,
    *,
    market_request: bool,
) -> dict[str, int | bool | str | None] | None:
    offset = 8
    collateral_usd_delta = _read_u64_at(data, offset)
    size_usd_delta = _read_u64_at(data, offset + 8)
    if collateral_usd_delta is None or size_usd_delta is None:
        return None
    offset += 16
    if market_request:
        request_type = "market"
        price_slippage = _read_u64_at(data, offset)
        if price_slippage is None:
            return None
        offset += 8
    else:
        request_type_raw = _read_u8_at(data, offset)
        if request_type_raw is None:
            return None
        request_type = _jupiter_request_type_name(request_type_raw)
        offset += 1
        option = _read_option_u64(data, offset)
        if option is None:
            return None
        price_slippage, offset = option
    option = _read_option_u64(data, offset)
    if option is None:
        return None
    jupiter_minimum_out, offset = option
    if market_request:
        trigger_price = None
        trigger_above_threshold = None
    else:
        option = _read_option_u64(data, offset)
        if option is None:
            return None
        trigger_price, offset = option
        bool_option = _read_option_bool(data, offset)
        if bool_option is None:
            return None
        trigger_above_threshold, offset = bool_option
    bool_option = _read_option_bool(data, offset)
    if bool_option is None:
        return None
    entire_position, offset = bool_option
    counter = _read_u64_at(data, offset)
    if counter is None:
        return None
    return {
        "collateral_usd_delta": collateral_usd_delta,
        "size_usd_delta": size_usd_delta,
        "request_type": request_type,
        "price_slippage": price_slippage,
        "jupiter_minimum_out": jupiter_minimum_out,
        "trigger_price": trigger_price,
        "trigger_above_threshold": trigger_above_threshold,
        "entire_position": entire_position,
        "counter": counter,
    }


def _jupiter_bool_param(data: bytes) -> bool | None:
    if len(data) != 9:
        return None
    return data[8] > 0


def _jupiter_request_type_name(value: int) -> str:
    if value == 0:
        return "market"
    if value == 1:
        return "trigger"
    return "unknown"


def _jupiter_position_side(value: object) -> PositionSide | None:
    if value == 1:
        return PositionSide.LONG
    if value == 2:
        return PositionSide.SHORT
    return None


def _jupiter_leverage(size_usd_delta: int, collateral_delta: int) -> Numeric:
    if collateral_delta <= 0:
        return 1
    return max(1, size_usd_delta / collateral_delta)


def _read_u8_at(data: bytes, offset: int) -> int | None:
    if offset + 1 > len(data):
        return None
    return data[offset]


def _read_u64_at(data: bytes, offset: int) -> int | None:
    if offset + 8 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 8], "little", signed=False)


def _read_option_u64(data: bytes, offset: int) -> tuple[int | None, int] | None:
    tag = _read_u8_at(data, offset)
    if tag is None:
        return None
    if tag == 0:
        return None, offset + 1
    if tag != 1:
        return None
    value = _read_u64_at(data, offset + 1)
    if value is None:
        return None
    return value, offset + 9


def _read_option_bool(data: bytes, offset: int) -> tuple[bool | None, int] | None:
    tag = _read_u8_at(data, offset)
    if tag is None:
        return None
    if tag == 0:
        return None, offset + 1
    if tag != 1:
        return None
    value = _read_u8_at(data, offset + 1)
    if value is None:
        return None
    return value > 0, offset + 2


def _read_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _read_optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _non_none_accounts(*accounts: str | None) -> tuple[str, ...]:
    return tuple(account for account in accounts if account is not None)


def _decode_whirlpool_swap_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    token_balance_mints: dict[str, str],
    inner_instructions: tuple[_InnerInstruction, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> MaterializedSwapAction | None:
    params = _decode_whirlpool_swap_params(ix.get("data"))
    if params is None:
        return None
    accounts = _decode_whirlpool_swap_accounts(
        ix,
        account_keys=account_keys,
        token_balance_mints=token_balance_mints,
        variant=params.variant,
    )
    if accounts is None:
        return None

    if params.a_to_b:
        source = accounts.token_owner_account_a
        destination = accounts.token_owner_account_b
        input_vault = accounts.token_vault_a
        output_vault = accounts.token_vault_b
        token_in = accounts.token_mint_a
        token_out = accounts.token_mint_b
    else:
        source = accounts.token_owner_account_b
        destination = accounts.token_owner_account_a
        input_vault = accounts.token_vault_b
        output_vault = accounts.token_vault_a
        token_in = accounts.token_mint_b
        token_out = accounts.token_mint_a

    actual_in, actual_out = _infer_whirlpool_inner_transfer_amounts(
        inner_instructions=inner_instructions,
        parent_instruction_index=instruction_index,
        account_keys=account_keys,
        source=source,
        destination=destination,
        input_vault=input_vault,
        output_vault=output_vault,
        fee_payer=fee_payer,
        signature=signature,
    )
    amount_in = actual_in
    amount_out: Numeric | None = actual_out
    if params.amount_specified_is_input:
        if amount_in is None:
            amount_in = params.amount
    else:
        if amount_out is None:
            amount_out = params.amount
        if amount_in is None:
            amount_in = params.other_amount_threshold

    metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )
    return MaterializedSwapAction(
        agent_id=accounts.token_authority or fee_payer,
        compute_unit_limit=compute_units,
        token_in=token_in or source or "",
        token_out=token_out or destination or "",
        amount_in=amount_in or 0,
        amount_out=amount_out,
        pool_id=accounts.whirlpool,
        source_token_account=source,
        destination_token_account=destination,
        protocol_program_id=WHIRLPOOL_PROGRAM,
        signature=signature,
        materialized_metadata=metadata,
    )


def _decode_whirlpool_swap_params(value: Any) -> _WhirlpoolSwapParams | None:
    data = _decode_instruction_data(value)
    if len(data) < _WHIRLPOOL_SWAP_DATA_LEN:
        return None
    discriminator = data[:8]
    if discriminator == _WHIRLPOOL_SWAP_DISCRIMINATOR:
        variant = "swap"
    elif discriminator == _WHIRLPOOL_SWAP_V2_DISCRIMINATOR:
        variant = "swap_v2"
    else:
        return None
    amount = int.from_bytes(data[8:16], "little", signed=False)
    other_amount_threshold = int.from_bytes(data[16:24], "little", signed=False)
    sqrt_price_limit = int.from_bytes(data[24:40], "little", signed=False)
    amount_specified_is_input = data[40] == 1
    a_to_b = data[41] == 1
    return _WhirlpoolSwapParams(
        variant=variant,
        amount=amount,
        other_amount_threshold=other_amount_threshold,
        sqrt_price_limit=sqrt_price_limit,
        amount_specified_is_input=amount_specified_is_input,
        a_to_b=a_to_b,
    )


def _decode_whirlpool_swap_accounts(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    token_balance_mints: dict[str, str],
    variant: str,
) -> _WhirlpoolSwapAccounts | None:
    account_count = _instruction_account_count(ix)
    if variant == "swap":
        if account_count < 11:
            return None
        token_authority = _instruction_account(ix, account_keys, 1)
        whirlpool = _instruction_account(ix, account_keys, 2)
        owner_a = _instruction_account(ix, account_keys, 3)
        vault_a = _instruction_account(ix, account_keys, 4)
        owner_b = _instruction_account(ix, account_keys, 5)
        vault_b = _instruction_account(ix, account_keys, 6)
        mint_a = _mint_for_token_account(token_balance_mints, owner_a, vault_a)
        mint_b = _mint_for_token_account(token_balance_mints, owner_b, vault_b)
    elif variant == "swap_v2":
        if account_count < 15:
            return None
        token_authority = _instruction_account(ix, account_keys, 3)
        whirlpool = _instruction_account(ix, account_keys, 4)
        mint_a = _instruction_account(ix, account_keys, 5)
        mint_b = _instruction_account(ix, account_keys, 6)
        owner_a = _instruction_account(ix, account_keys, 7)
        vault_a = _instruction_account(ix, account_keys, 8)
        owner_b = _instruction_account(ix, account_keys, 9)
        vault_b = _instruction_account(ix, account_keys, 10)
    else:
        return None

    if (
        token_authority is None
        or whirlpool is None
        or owner_a is None
        or owner_b is None
        or vault_a is None
        or vault_b is None
    ):
        return None
    if variant == "swap_v2" and (mint_a is None or mint_b is None):
        return None
    return _WhirlpoolSwapAccounts(
        token_authority=token_authority,
        whirlpool=whirlpool,
        token_mint_a=mint_a,
        token_mint_b=mint_b,
        token_owner_account_a=owner_a,
        token_vault_a=vault_a,
        token_owner_account_b=owner_b,
        token_vault_b=vault_b,
    )


def _mint_for_token_account(
    token_balance_mints: dict[str, str],
    owner_account: str | None,
    vault_account: str | None,
) -> str | None:
    if owner_account is not None and owner_account in token_balance_mints:
        return token_balance_mints[owner_account]
    if vault_account is not None and vault_account in token_balance_mints:
        return token_balance_mints[vault_account]
    return None


def _infer_whirlpool_inner_transfer_amounts(
    *,
    inner_instructions: tuple[_InnerInstruction, ...],
    parent_instruction_index: int,
    account_keys: tuple[str, ...],
    source: str | None,
    destination: str | None,
    input_vault: str | None,
    output_vault: str | None,
    fee_payer: str,
    signature: str | None,
) -> tuple[int | None, int | None]:
    amount_in: int | None = None
    amount_out: int | None = None
    for inner in inner_instructions:
        if inner.parent_index != parent_instruction_index:
            continue
        program_id = _resolve_instruction_program_id(inner.instruction, account_keys)
        if program_id not in TOKEN_PROGRAM_IDS:
            continue
        transfer = _decode_token_transfer_instruction(
            inner.instruction,
            account_keys=account_keys,
            program_id=program_id,
            fee_payer=fee_payer,
            compute_units=None,
            fee_lamports=None,
            signature=signature,
            slot=None,
            transaction_index=0,
            instruction_index=0,
            program_ids=(program_id,),
            instruction_count=1,
        )
        if transfer is None:
            continue
        if transfer.source == source and transfer.destination == input_vault:
            amount_in = transfer.amount
        elif transfer.source == output_vault and transfer.destination == destination:
            amount_out = transfer.amount
    return amount_in, amount_out


def _decode_dlmm_swap_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    token_balance_mints: dict[str, str],
    inner_instructions: tuple[_InnerInstruction, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> MaterializedSwapAction | None:
    params = _decode_dlmm_swap_params(ix.get("data"))
    if params is None:
        return None
    accounts = _decode_dlmm_swap_accounts(ix, account_keys=account_keys)
    if accounts is None:
        return None

    (
        actual_in,
        actual_out,
        inferred_token_in,
        inferred_token_out,
    ) = _infer_dlmm_inner_transfer_amounts(
        inner_instructions=inner_instructions,
        parent_instruction_index=instruction_index,
        account_keys=account_keys,
        reserve_x=accounts.reserve_x,
        reserve_y=accounts.reserve_y,
        user_token_in=accounts.user_token_in,
        user_token_out=accounts.user_token_out,
        token_x_mint=accounts.token_x_mint,
        token_y_mint=accounts.token_y_mint,
        fee_payer=fee_payer,
        signature=signature,
    )

    amount_in = actual_in
    amount_out: Numeric | None = actual_out
    if params.amount_in is not None and amount_in is None:
        amount_in = params.amount_in
    if params.out_amount is not None:
        if amount_out is None:
            amount_out = params.out_amount
        if amount_in is None:
            amount_in = params.max_in_amount

    token_in = (
        inferred_token_in
        or _mint_for_token_account(token_balance_mints, accounts.user_token_in, None)
        or accounts.user_token_in
        or ""
    )
    token_out = (
        inferred_token_out
        or _mint_for_token_account(token_balance_mints, accounts.user_token_out, None)
        or accounts.user_token_out
        or ""
    )

    metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )
    reserves = tuple(
        account
        for account in (accounts.reserve_x, accounts.reserve_y)
        if account is not None
    )
    return MaterializedSwapAction(
        agent_id=accounts.user or fee_payer,
        compute_unit_limit=compute_units,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in or 0,
        amount_out=amount_out,
        pool_id=accounts.lb_pair,
        source_token_account=accounts.user_token_in,
        destination_token_account=accounts.user_token_out,
        protocol_program_id=METEORA_DLMM_PROGRAM,
        pool_reserve_accounts=reserves,
        active_bin_id=params.active_bin_id,
        bin_array_bitmap_extension=accounts.bin_array_bitmap_extension,
        signature=signature,
        materialized_metadata=metadata,
    )


def _decode_dlmm_swap_params(value: Any) -> _DlmmSwapParams | None:
    data = _decode_instruction_data(value)
    if len(data) < 8:
        return None
    discriminator = data[:8]
    if discriminator in _DLMM_EXACT_IN_DISCRIMINATORS:
        if len(data) < _DLMM_TWO_U64_DATA_LEN:
            return None
        variant = "swap2" if discriminator == _DLMM_SWAP2_DISCRIMINATOR else "swap"
        return _DlmmSwapParams(
            variant=variant,
            amount_in=int.from_bytes(data[8:16], "little", signed=False),
            min_amount_out=int.from_bytes(data[16:24], "little", signed=False),
            max_in_amount=None,
            out_amount=None,
            active_bin_id=None,
        )
    if discriminator in _DLMM_EXACT_OUT_DISCRIMINATORS:
        if len(data) < _DLMM_TWO_U64_DATA_LEN:
            return None
        variant = (
            "swap_exact_out2"
            if discriminator == _DLMM_SWAP_EXACT_OUT2_DISCRIMINATOR
            else "swap_exact_out"
        )
        return _DlmmSwapParams(
            variant=variant,
            amount_in=None,
            min_amount_out=None,
            max_in_amount=int.from_bytes(data[8:16], "little", signed=False),
            out_amount=int.from_bytes(data[16:24], "little", signed=False),
            active_bin_id=None,
        )
    if discriminator in _DLMM_PRICE_IMPACT_DISCRIMINATORS:
        if len(data) < _DLMM_PRICE_IMPACT_MIN_DATA_LEN:
            return None
        active_id: int | None = None
        option_tag = data[16]
        if option_tag == 1:
            if len(data) < 8 + 8 + 1 + 4 + 2:
                return None
            active_id = int.from_bytes(data[17:21], "little", signed=True)
        elif option_tag != 0:
            return None
        variant = (
            "swap_with_price_impact2"
            if discriminator == _DLMM_SWAP_WITH_PRICE_IMPACT2_DISCRIMINATOR
            else "swap_with_price_impact"
        )
        return _DlmmSwapParams(
            variant=variant,
            amount_in=int.from_bytes(data[8:16], "little", signed=False),
            min_amount_out=None,
            max_in_amount=None,
            out_amount=None,
            active_bin_id=active_id,
        )
    return None


def _decode_dlmm_swap_accounts(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
) -> _DlmmSwapAccounts | None:
    if _instruction_account_count(ix) < 11:
        return None
    lb_pair = _instruction_account(ix, account_keys, 0)
    reserve_x = _instruction_account(ix, account_keys, 2)
    reserve_y = _instruction_account(ix, account_keys, 3)
    user_token_in = _instruction_account(ix, account_keys, 4)
    user_token_out = _instruction_account(ix, account_keys, 5)
    user = _instruction_account(ix, account_keys, 10)
    if (
        lb_pair is None
        or reserve_x is None
        or reserve_y is None
        or user_token_in is None
        or user_token_out is None
        or user is None
    ):
        return None
    return _DlmmSwapAccounts(
        lb_pair=lb_pair,
        bin_array_bitmap_extension=_instruction_account(ix, account_keys, 1),
        reserve_x=reserve_x,
        reserve_y=reserve_y,
        user_token_in=user_token_in,
        user_token_out=user_token_out,
        token_x_mint=_instruction_account(ix, account_keys, 6),
        token_y_mint=_instruction_account(ix, account_keys, 7),
        oracle=_instruction_account(ix, account_keys, 8),
        user=user,
    )


def _infer_dlmm_inner_transfer_amounts(
    *,
    inner_instructions: tuple[_InnerInstruction, ...],
    parent_instruction_index: int,
    account_keys: tuple[str, ...],
    reserve_x: str | None,
    reserve_y: str | None,
    user_token_in: str | None,
    user_token_out: str | None,
    token_x_mint: str | None,
    token_y_mint: str | None,
    fee_payer: str,
    signature: str | None,
) -> tuple[int | None, int | None, str | None, str | None]:
    amount_in: int | None = None
    amount_out: int | None = None
    input_reserve: str | None = None
    output_reserve: str | None = None
    reserve_set = {account for account in (reserve_x, reserve_y) if account is not None}
    for inner in inner_instructions:
        if inner.parent_index != parent_instruction_index:
            continue
        program_id = _resolve_instruction_program_id(inner.instruction, account_keys)
        if program_id not in TOKEN_PROGRAM_IDS:
            continue
        transfer = _decode_token_transfer_instruction(
            inner.instruction,
            account_keys=account_keys,
            program_id=program_id,
            fee_payer=fee_payer,
            compute_units=None,
            fee_lamports=None,
            signature=signature,
            slot=None,
            transaction_index=0,
            instruction_index=0,
            program_ids=(program_id,),
            instruction_count=1,
        )
        if transfer is None:
            continue
        if transfer.source == user_token_in and transfer.destination in reserve_set:
            amount_in = transfer.amount
            input_reserve = transfer.destination
        elif transfer.destination == user_token_out and transfer.source in reserve_set:
            amount_out = transfer.amount
            output_reserve = transfer.source

    token_in: str | None = None
    token_out: str | None = None
    if input_reserve == reserve_x:
        token_in = token_x_mint
    elif input_reserve == reserve_y:
        token_in = token_y_mint
    if output_reserve == reserve_x:
        token_out = token_x_mint
    elif output_reserve == reserve_y:
        token_out = token_y_mint
    return amount_in, amount_out, token_in, token_out


def _decode_raydium_swap_instruction(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
    token_balance_mints: dict[str, str],
    inner_instructions: tuple[_InnerInstruction, ...],
    fee_payer: str,
    compute_units: int | None,
    fee_lamports: int | None,
    signature: str | None,
    slot: int | None,
    transaction_index: int,
    instruction_index: int,
    program_ids: tuple[str, ...],
    instruction_count: int,
) -> MaterializedSwapAction | None:
    params = _decode_raydium_swap_params(ix.get("data"))
    if params is None:
        return None
    accounts = _decode_raydium_swap_accounts(ix, account_keys=account_keys)
    if accounts is None:
        return None

    (
        actual_in,
        actual_out,
        inferred_token_in,
        inferred_token_out,
    ) = _infer_raydium_inner_transfer_amounts(
        inner_instructions=inner_instructions,
        parent_instruction_index=instruction_index,
        account_keys=account_keys,
        pool_coin_token_account=accounts.pool_coin_token_account,
        pool_pc_token_account=accounts.pool_pc_token_account,
        user_source_token_account=accounts.user_source_token_account,
        user_destination_token_account=accounts.user_destination_token_account,
        token_balance_mints=token_balance_mints,
        fee_payer=fee_payer,
        signature=signature,
    )

    amount_in = actual_in
    amount_out: Numeric | None = actual_out
    if params.amount_in is not None and amount_in is None:
        amount_in = params.amount_in
    if params.amount_out is not None:
        if amount_out is None:
            amount_out = params.amount_out
        if amount_in is None:
            amount_in = params.max_amount_in

    token_in = (
        inferred_token_in
        or _mint_for_token_account(
            token_balance_mints,
            accounts.user_source_token_account,
            None,
        )
        or accounts.user_source_token_account
        or ""
    )
    token_out = (
        inferred_token_out
        or _mint_for_token_account(
            token_balance_mints,
            accounts.user_destination_token_account,
            None,
        )
        or accounts.user_destination_token_account
        or ""
    )

    metadata = MaterializedActionMetadata(
        decode_status=ActionDecodeStatus.DECODED,
        signature=signature,
        slot=slot,
        transaction_index=transaction_index,
        instruction_index=instruction_index,
        program_ids=program_ids,
        instruction_count=instruction_count,
        decoded_instruction_count=1,
        fee_lamports=fee_lamports,
    )
    reserves = tuple(
        account
        for account in (
            accounts.pool_coin_token_account,
            accounts.pool_pc_token_account,
        )
        if account is not None
    )
    return MaterializedSwapAction(
        agent_id=accounts.user_source_owner or fee_payer,
        compute_unit_limit=compute_units,
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in or 0,
        amount_out=amount_out,
        pool_id=accounts.amm,
        source_token_account=accounts.user_source_token_account,
        destination_token_account=accounts.user_destination_token_account,
        protocol_program_id=RAYDIUM_AMM_V4_PROGRAM,
        pool_reserve_accounts=reserves,
        signature=signature,
        materialized_metadata=metadata,
    )


def _decode_raydium_swap_params(value: Any) -> _RaydiumSwapParams | None:
    data = _decode_instruction_data(value)
    if len(data) < _RAYDIUM_SWAP_DATA_LEN:
        return None
    tag = data[0]
    first_amount = int.from_bytes(data[1:9], "little", signed=False)
    second_amount = int.from_bytes(data[9:17], "little", signed=False)
    if tag == _RAYDIUM_SWAP_BASE_IN_TAG:
        return _RaydiumSwapParams(
            variant="swap_base_in",
            amount_in=first_amount,
            min_amount_out=second_amount,
            max_amount_in=None,
            amount_out=None,
        )
    if tag == _RAYDIUM_SWAP_BASE_OUT_TAG:
        return _RaydiumSwapParams(
            variant="swap_base_out",
            amount_in=None,
            min_amount_out=None,
            max_amount_in=first_amount,
            amount_out=second_amount,
        )
    return None


def _decode_raydium_swap_accounts(
    ix: dict[str, Any],
    *,
    account_keys: tuple[str, ...],
) -> _RaydiumSwapAccounts | None:
    account_count = _instruction_account_count(ix)
    if account_count >= 18:
        amm_index = 1
        pool_coin_index = 5
        pool_pc_index = 6
        user_source_index = 15
        user_destination_index = 16
        user_owner_index = 17
    elif account_count >= 17:
        amm_index = 1
        pool_coin_index = 4
        pool_pc_index = 5
        user_source_index = 14
        user_destination_index = 15
        user_owner_index = 16
    else:
        return None

    amm = _instruction_account(ix, account_keys, amm_index)
    pool_coin = _instruction_account(ix, account_keys, pool_coin_index)
    pool_pc = _instruction_account(ix, account_keys, pool_pc_index)
    user_source = _instruction_account(ix, account_keys, user_source_index)
    user_destination = _instruction_account(ix, account_keys, user_destination_index)
    user_owner = _instruction_account(ix, account_keys, user_owner_index)
    if amm is None or pool_coin is None or pool_pc is None:
        return None
    if user_source is None or user_destination is None:
        return None
    if user_owner is None:
        return None
    return _RaydiumSwapAccounts(
        amm=amm,
        pool_coin_token_account=pool_coin,
        pool_pc_token_account=pool_pc,
        user_source_token_account=user_source,
        user_destination_token_account=user_destination,
        user_source_owner=user_owner,
    )


def _infer_raydium_inner_transfer_amounts(
    *,
    inner_instructions: tuple[_InnerInstruction, ...],
    parent_instruction_index: int,
    account_keys: tuple[str, ...],
    pool_coin_token_account: str | None,
    pool_pc_token_account: str | None,
    user_source_token_account: str | None,
    user_destination_token_account: str | None,
    token_balance_mints: dict[str, str],
    fee_payer: str,
    signature: str | None,
) -> tuple[int | None, int | None, str | None, str | None]:
    amount_in: int | None = None
    amount_out: int | None = None
    input_reserve: str | None = None
    output_reserve: str | None = None
    reserve_set = {
        account
        for account in (pool_coin_token_account, pool_pc_token_account)
        if account is not None
    }
    for inner in inner_instructions:
        if inner.parent_index != parent_instruction_index:
            continue
        program_id = _resolve_instruction_program_id(inner.instruction, account_keys)
        if program_id not in TOKEN_PROGRAM_IDS:
            continue
        transfer = _decode_token_transfer_instruction(
            inner.instruction,
            account_keys=account_keys,
            program_id=program_id,
            fee_payer=fee_payer,
            compute_units=None,
            fee_lamports=None,
            signature=signature,
            slot=None,
            transaction_index=0,
            instruction_index=0,
            program_ids=(program_id,),
            instruction_count=1,
        )
        if transfer is None:
            continue
        if (
            transfer.source == user_source_token_account
            and transfer.destination in reserve_set
        ):
            amount_in = transfer.amount
            input_reserve = transfer.destination
        elif (
            transfer.destination == user_destination_token_account
            and transfer.source in reserve_set
        ):
            amount_out = transfer.amount
            output_reserve = transfer.source

    token_in = _mint_for_token_account(token_balance_mints, None, input_reserve)
    token_out = _mint_for_token_account(token_balance_mints, None, output_reserve)
    return amount_in, amount_out, token_in, token_out


def _parsed_token_amount(info: dict[str, Any]) -> int | None:
    raw = info.get("amount")
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    if isinstance(raw, int):
        return raw
    token_amount = info.get("tokenAmount")
    if isinstance(token_amount, dict):
        amount = token_amount.get("amount")
        if isinstance(amount, str):
            try:
                return int(amount)
            except ValueError:
                return None
        if isinstance(amount, int):
            return amount
    return None


def _parsed_lamports(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _instruction_account(
    ix: dict[str, Any],
    account_keys: tuple[str, ...],
    offset: int,
) -> str | None:
    accounts = ix.get("accounts")
    if not isinstance(accounts, list) or offset >= len(accounts):
        return None
    account = accounts[offset]
    if isinstance(account, str):
        return account
    if isinstance(account, int) and 0 <= account < len(account_keys):
        return account_keys[account]
    if isinstance(account, dict):
        return _read_str(account.get("pubkey"))
    return None


def _instruction_account_count(ix: dict[str, Any]) -> int:
    accounts = ix.get("accounts")
    return len(accounts) if isinstance(accounts, list) else 0


def _decode_instruction_data(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, list) and len(value) >= 2:
        payload, encoding = value[0], str(value[1]).lower()
        if not isinstance(payload, str):
            return b""
        if encoding in {"base64", "base64+zstd"}:
            import base64

            try:
                return base64.b64decode(payload)
            except (TypeError, ValueError):
                return b""
        if encoding == "base58":
            return _decode_base58(payload)
        return b""
    if isinstance(value, str):
        return _decode_base58(value)
    return b""


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {ch: idx for idx, ch in enumerate(_BASE58_ALPHABET)}


def _decode_base58(value: str) -> bytes:
    if value == "":
        return b""
    num = 0
    for char in value:
        digit = _BASE58_INDEX.get(char)
        if digit is None:
            return b""
        num = num * 58 + digit
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def _read_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# Tag retained for module-level introspection by future grep-for-gaps audits.
_DECODER_GAP_MARKER = "CALIBRATE-3.x"
