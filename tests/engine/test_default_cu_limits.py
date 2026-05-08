"""Per-action-type CU-default registry (`DEFAULT_CU_LIMITS`) in `engine/gas.py`.

These defaults are synthetic priors — stand-ins until Phase 2.1 calibrates
against replayed mainnet transactions. The registry is consumed by
`ComputeUnitCost.breakdown()` (task 0.4.5) and exists so per-type fallbacks
don't all collapse to a single number.
"""

from __future__ import annotations

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
    OpenPositionAction,
    OrderAction,
    RepayAction,
    SingleAssetAction,
    StakeAction,
    SwapAction,
    UnstakeAction,
    WithdrawCollateralAction,
)
from defi_sim.engine.gas import (
    DEFAULT_CU_LIMITS,
    DEFAULT_CU_PRICE_MICRO_LAMPORTS,
)


def test_default_cu_price_is_zero_baseline() -> None:
    assert DEFAULT_CU_PRICE_MICRO_LAMPORTS == 0


def test_default_cu_limits_covers_every_action_subclass() -> None:
    expected = {
        SwapAction,
        SingleAssetAction,
        BundleAction,
        LPAction,
        OrderAction,
        DepositCollateralAction,
        WithdrawCollateralAction,
        BorrowAction,
        RepayAction,
        LiquidateAction,
        OpenPositionAction,
        ClosePositionAction,
        AdjustMarginAction,
        StakeAction,
        UnstakeAction,
        ClaimRewardsAction,
        GovernanceAction,
        AtomicAction,
        FlashLoanAction,
        MultiMarketAction,
        ConditionalAction,
    }
    missing = expected - set(DEFAULT_CU_LIMITS.keys())
    assert not missing, f"DEFAULT_CU_LIMITS missing entries for: {missing}"


def test_default_cu_limits_are_positive_ints() -> None:
    for action_cls, limit in DEFAULT_CU_LIMITS.items():
        assert isinstance(limit, int), f"{action_cls.__name__}: limit must be int"
        assert limit > 0, f"{action_cls.__name__}: limit must be > 0"


def test_swap_default_matches_prd_baseline() -> None:
    assert DEFAULT_CU_LIMITS[SwapAction] == 200_000


def test_bundle_default_is_largest_baseline() -> None:
    # BundleAction (600k) and AtomicAction (600k) are the heaviest defaults
    # in the registry — wrappers that bundle multiple inner actions.
    assert DEFAULT_CU_LIMITS[BundleAction] == 600_000
    assert DEFAULT_CU_LIMITS[BundleAction] == max(DEFAULT_CU_LIMITS.values())


def test_registry_keys_are_action_subclasses() -> None:
    for action_cls in DEFAULT_CU_LIMITS:
        assert issubclass(action_cls, Action), (
            f"{action_cls.__name__} is not an Action subclass"
        )
