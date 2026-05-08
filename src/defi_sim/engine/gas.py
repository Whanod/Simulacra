"""Transaction / execution cost models.

The module name is retained for backwards compatibility. The abstractions
are intentionally network-neutral and can be used for gas, compute-unit,
or any other execution-layer fee market.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from defi_sim.core.types import (
    Action,
    AdjustMarginAction,
    AtomicAction,
    BorrowAction,
    BundleAction,
    ClaimRewardsAction,
    ClosePositionAction,
    ConditionalAction,
    DepositCollateralAction,
    FlashLoanAction,
    GovernanceAction,
    LiquidateAction,
    LPAction,
    MultiMarketAction,
    Numeric,
    OpenPositionAction,
    OrderAction,
    RepayAction,
    SingleAssetAction,
    StakeAction,
    SwapAction,
    TokenId,
    UnstakeAction,
    WithdrawCollateralAction,
)


# Synthetic per-action-type CU-limit priors. These are NOT claims about
# observed mainnet CU usage — they are stand-ins until Phase 2.1 calibrates
# against replayed transactions. `breakdown()` consumes this registry in
# task 0.4.5; the action helper `Action.priority_lamports()` continues to
# use the simpler `DEFAULT_CU_LIMIT_FALLBACK` from `core.types` for actions
# constructed outside the engine. Source metadata (`cu_limit_source`) is
# attached when `breakdown()` resolves a fallback (task 0.4.5).
DEFAULT_CU_LIMITS: dict[type[Action], int] = {
    SwapAction: 200_000,
    SingleAssetAction: 80_000,
    BundleAction: 600_000,
    LPAction: 250_000,
    OrderAction: 100_000,
    DepositCollateralAction: 100_000,
    WithdrawCollateralAction: 100_000,
    BorrowAction: 150_000,
    RepayAction: 100_000,
    LiquidateAction: 300_000,
    OpenPositionAction: 200_000,
    ClosePositionAction: 150_000,
    AdjustMarginAction: 120_000,
    StakeAction: 80_000,
    UnstakeAction: 80_000,
    ClaimRewardsAction: 60_000,
    GovernanceAction: 50_000,
    AtomicAction: 600_000,
    FlashLoanAction: 400_000,
    MultiMarketAction: 200_000,
    ConditionalAction: 200_000,
}

DEFAULT_CU_PRICE_MICRO_LAMPORTS: int = 0


# CALIBRATE-2.1: marginal CU cost of Address Lookup Table resolution. Real
# Solana cost is approximately 100 CU per used table plus 10 CU per resolved
# entry — replayed-tx calibration in Phase 2.1 will refine these.
ALT_LOOKUP_CU_PER_TABLE: int = 100
ALT_LOOKUP_CU_PER_ENTRY: int = 10


def alt_lookup_cu(num_used_tables: int, num_resolved_entries: int) -> int:
    """Marginal CU cost charged for ALT (Address Lookup Table) resolution.

    Approximates Solana's runtime cost of dereferencing ALT entries during
    transaction loading: a per-table overhead plus a per-resolved-entry
    overhead. # CALIBRATE-2.1
    """
    if num_used_tables < 0 or num_resolved_entries < 0:
        raise ValueError("ALT counts must be non-negative")
    return (
        num_used_tables * ALT_LOOKUP_CU_PER_TABLE
        + num_resolved_entries * ALT_LOOKUP_CU_PER_ENTRY
    )


@dataclass(frozen=True)
class FeeBreakdown:
    """Lamport-denominated split of a Solana transaction fee.

    Base fee is `5_000 * num_required_signatures`, divided 50% burned /
    50% to validator (floor / `base_fee - floor` so odd values land safely).
    Priority fee is `ceil(price_micro_lamports * cu_limit / 1_000_000)`
    and accrues entirely to the validator. `total_lamports` is the sum.
    Consumed by `ComputeUnitCost.breakdown()` (task 0.4.5) and the
    validator-economics path (Phase 1.10).
    """

    base_fee_lamports: int
    base_fee_burned_lamports: int
    base_fee_to_validator_lamports: int
    priority_fee_lamports: int
    total_lamports: int
    # Provenance of the CU limit used in this fee computation. Reports must
    # surface this so synthetic priors are not mistaken for observed mainnet
    # CU usage. Values: ``"explicit"`` (action specified the limit),
    # ``"synthetic_default"`` (resolved from ``DEFAULT_CU_LIMITS`` registry,
    # i.e., a Phase 0.4 prior pending Phase 2.1 calibration), or
    # ``"legacy_fallback"`` (deprecated ``ComputeUnitCost(default_units=...)``
    # path used because the action subtype is not in the registry).
    cu_limit_source: str = "explicit"


_LEGACY_CTOR_SENTINEL: object = object()


def _priority_lamports(action: Action) -> Numeric:
    """CU-aware priority fee in lamports."""
    helper = getattr(action, "priority_lamports", None)
    if callable(helper):
        return helper()
    return 0


class TransactionCostModel(ABC):
    @abstractmethod
    def cost(self, action: Action, round: int) -> Numeric:
        """Return the execution-layer cost for this action at this round."""
        ...


class ZeroCost(TransactionCostModel):
    """No transaction costs."""

    def cost(self, action: Action, round: int) -> Numeric:
        return 0


class FixedCost(TransactionCostModel):
    """Constant cost per action."""

    def __init__(self, cost_per_action: Numeric):
        self._cost = cost_per_action

    def cost(self, action: Action, round: int) -> Numeric:
        return self._cost


class TypedCost(TransactionCostModel):
    """Different costs per action type."""

    def __init__(self, costs: dict[type, Numeric], default_cost: Numeric = 0):
        self._costs = costs
        self._default = default_cost

    def cost(self, action: Action, round: int) -> Numeric:
        return self._costs.get(type(action), self._default)


class EIP1559Cost(TransactionCostModel):
    """Ethereum-like base fee + priority bid model."""

    def __init__(
        self,
        base_fee: Numeric,
        target_actions_per_round: int = 50,
        adjustment_factor: int = 8,
    ):
        self._base_fee = base_fee
        self._target = target_actions_per_round
        self._factor = adjustment_factor

    def cost(self, action: Action, round: int) -> Numeric:
        return self._base_fee + _priority_lamports(action)

    def update_base_fee(self, num_actions: int) -> None:
        """Adjust base fee after each round based on utilization."""
        if isinstance(self._base_fee, float):
            if num_actions > self._target:
                self._base_fee += self._base_fee / self._factor
            elif num_actions < self._target:
                self._base_fee = max(0.001, self._base_fee - self._base_fee / self._factor)
        else:
            if num_actions > self._target:
                self._base_fee += self._base_fee // self._factor
            elif num_actions < self._target:
                self._base_fee = max(1, self._base_fee - self._base_fee // self._factor)


class ComputeUnitCost(TransactionCostModel):
    """Solana mainnet transaction-fee model.

    Lamport total per the Solana fee schedule:
        ``5_000 * num_required_signatures + ceil(price_micro_lamports * cu_limit / 1_000_000)``.

    Where:
      * ``5_000`` is the per-signature base fee in lamports (Solana
        ``LAMPORTS_PER_SIGNATURE`` constant; ``num_required_signatures``
        counts every fee-bearing signature on the transaction, including
        precompile verification signatures, not just unique wallet
        signers).
      * ``price_micro_lamports`` is the prioritization-fee price in
        micro-lamports per compute unit, set on the transaction via the
        ``ComputeBudget`` ``SetComputeUnitPrice`` instruction.
      * ``cu_limit`` is the requested compute-unit budget, set via
        ``ComputeBudget`` ``SetComputeUnitLimit`` (max 1.4M CU per tx as
        of mainnet-beta; capped per slot and per writable account by
        cluster limits — Phase 1.2 enforces those budgets).
      * The priority fee uses ceil division to match runtime behaviour.

    The base fee is split 50% burned / 50% to the validator; the priority
    fee accrues entirely to the validator. ``breakdown()`` returns the
    economic split as a ``FeeBreakdown`` for the validator-economics path
    (Phase 1.10) and other consumers.

    References:
      * Solana docs — Transaction fees:
        https://solana.com/docs/core/fees
      * Solana docs — Prioritization fees:
        https://solana.com/developers/guides/advanced/how-to-use-priority-fees
      * SIMD-0096 / fee burn changes:
        https://github.com/solana-foundation/solana-improvement-documents

    Per-action-type CU defaults come from ``DEFAULT_CU_LIMITS``; when an
    action's subtype is not in the registry, ``default_units`` (the legacy
    constructor arg, retained for one release cycle) is used as the final
    fallback. Constructor args ``unit_costs`` / ``default_units`` /
    ``base_cost`` are all deprecated. Passing any of them emits a
    ``DeprecationWarning``; ``unit_costs`` and ``base_cost`` are
    accepted-but-ignored, while ``default_units`` is still honoured as
    the unregistered-subtype fallback for the deprecation window.
    """

    def __init__(
        self,
        unit_costs: dict[type, Numeric] | None = _LEGACY_CTOR_SENTINEL,  # type: ignore[assignment]
        default_units: Numeric = _LEGACY_CTOR_SENTINEL,  # type: ignore[assignment]
        base_cost: Numeric = _LEGACY_CTOR_SENTINEL,  # type: ignore[assignment]
        tokens: Mapping[TokenId, Any] | None = None,
    ):
        provided = [
            name
            for name, value in (
                ("unit_costs", unit_costs),
                ("default_units", default_units),
                ("base_cost", base_cost),
            )
            if value is not _LEGACY_CTOR_SENTINEL
        ]
        if provided:
            warnings.warn(
                "ComputeUnitCost constructor arguments "
                f"{provided} are deprecated and will be removed in Phase 1 "
                "cleanup. The Solana fee formula is parameter-free; set "
                "compute_unit_limit / compute_unit_price_micro_lamports on "
                "the Action instead, and rely on DEFAULT_CU_LIMITS for "
                "per-action-type fallbacks. unit_costs and base_cost are "
                "now ignored; default_units is still honoured as the "
                "unregistered-subtype fallback for this release cycle only.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._unit_costs = (
            unit_costs if unit_costs is not _LEGACY_CTOR_SENTINEL and unit_costs else {}
        )
        self._default_units = (
            default_units if default_units is not _LEGACY_CTOR_SENTINEL else 1
        )
        self._base_cost = base_cost if base_cost is not _LEGACY_CTOR_SENTINEL else 0
        # US-007 (PRD line 584): SPL Token-2022 transfer-hook overhead.
        # When supplied, ``tokens`` lets ``breakdown()`` charge each
        # SPL-2022 transfer-hook'd token's ``additional_cu_per_transfer``
        # (priced through the action's CU price) plus
        # ``additional_lamports_per_transfer`` flat surcharge per token
        # touched by the action. Empty / None disables the surcharge
        # path so non-Solana callers and pre-1.9 fixtures stay byte-equal.
        self._tokens: Mapping[TokenId, Any] = tokens or {}

    _TOKEN_ATTRS: tuple[str, ...] = (
        "token_in",
        "token_out",
        "token",
        "asset",
        "collateral",
        "base",
        "quote",
    )

    def _hook_surcharge(
        self, action: Action, price_micro: Numeric
    ) -> tuple[int, int]:
        """Return (extra_priority_lamports, extra_flat_lamports) for SPL-2022 hooks.

        Walks the action's token-bearing fields. For each referenced token
        whose ``standard == "spl_2022"`` and ``transfer_hook is not None``,
        the configured ``additional_cu_per_transfer`` contributes to the
        priority-fee component (priced through ``price_micro``) and the
        configured ``additional_lamports_per_transfer`` is added flat.
        """
        if not self._tokens:
            return 0, 0
        extra_cu = 0
        extra_lamports = 0
        for attr in self._TOKEN_ATTRS:
            tok_id = getattr(action, attr, None)
            if not tok_id:
                continue
            tok = self._tokens.get(tok_id)
            if tok is None:
                continue
            if getattr(tok, "standard", None) != "spl_2022":
                continue
            hook = getattr(tok, "transfer_hook", None)
            if hook is None:
                continue
            extra_cu += int(getattr(hook, "additional_cu_per_transfer", 0) or 0)
            extra_lamports += int(
                getattr(hook, "additional_lamports_per_transfer", 0) or 0
            )
        priority_extra = -(-(int(price_micro) * extra_cu) // 1_000_000) if extra_cu else 0
        return priority_extra, extra_lamports

    def breakdown(self, action: Action, round: int) -> FeeBreakdown:
        num_signers = getattr(action, "num_required_signatures", 1) or 1
        cu_limit = getattr(action, "compute_unit_limit", None)
        if cu_limit is not None:
            cu_limit_source = "explicit"
        elif type(action) in DEFAULT_CU_LIMITS:
            cu_limit = DEFAULT_CU_LIMITS[type(action)]
            cu_limit_source = "synthetic_default"
        else:
            cu_limit = self._default_units
            cu_limit_source = "legacy_fallback"
        price_micro = getattr(action, "compute_unit_price_micro_lamports", None)
        if price_micro is None:
            price_micro = DEFAULT_CU_PRICE_MICRO_LAMPORTS
        base_fee = 5_000 * num_signers
        priority_fee = -(-(price_micro * cu_limit) // 1_000_000)
        hook_priority_extra, hook_flat_extra = self._hook_surcharge(action, price_micro)
        # CALIBRATE-2.1: ALT lookup adds ~100 CU per used table + ~10 CU
        # per resolved entry; priced through the action's CU price.
        num_alt_tables = len(getattr(action, "lookup_tables", ()) or ())
        num_alt_entries = int(getattr(action, "alt_resolved_entries", 0) or 0)
        alt_cu = alt_lookup_cu(num_alt_tables, num_alt_entries)
        alt_priority_extra = (
            -(-(int(price_micro) * alt_cu) // 1_000_000) if alt_cu else 0
        )
        priority_fee = (
            priority_fee + hook_priority_extra + hook_flat_extra + alt_priority_extra
        )
        burned = base_fee // 2
        validator_base = base_fee - burned
        return FeeBreakdown(
            base_fee_lamports=base_fee,
            base_fee_burned_lamports=burned,
            base_fee_to_validator_lamports=validator_base,
            priority_fee_lamports=priority_fee,
            total_lamports=base_fee + priority_fee,
            cu_limit_source=cu_limit_source,
        )

    def cost(self, action: Action, round: int) -> Numeric:
        return self.breakdown(action, round).total_lamports


# Backwards-compatible aliases
GasCostModel = TransactionCostModel
ZeroGas = ZeroCost
FixedGas = FixedCost
TypedGas = TypedCost
EIP1559Gas = EIP1559Cost
