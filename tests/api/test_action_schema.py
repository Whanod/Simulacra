"""Round-trip tests for ActionSchema mirrors (PRD task 0.4.7 slice 2).

ActionSchema and per-subtype mirrors live in
``src/defi_sim_api/schemas.py`` and bridge the API surface to the engine
dataclasses in ``defi_sim.core.types``.
"""
from __future__ import annotations

import pytest

from defi_sim.core.types import (
    BundleAction,
    LPAction,
    LPActionType,
    OrderAction,
    OrderSide,
    Side,
    SingleAssetAction,
    SwapAction,
)
from defi_sim_api.schemas import (
    ActionSchema,
    BundleActionSchema,
    LPActionSchema,
    OrderActionSchema,
    SingleAssetActionSchema,
    SwapActionSchema,
)


def _build_swap() -> SwapActionSchema:
    return SwapActionSchema(
        agent_id="alice",
        num_required_signatures=2,
        compute_unit_limit=180_000,
        compute_unit_price_micro_lamports=750_000,
        token_in="SOL",
        token_out="USDC",
        amount_in=1_000,
    )


def _build_single_asset() -> SingleAssetActionSchema:
    return SingleAssetActionSchema(
        agent_id="bob",
        num_required_signatures=1,
        compute_unit_limit=80_000,
        compute_unit_price_micro_lamports=10_000,
        asset="SOL",
        collateral="USDC",
        amount=42,
        side=Side.SELL,
    )


def _build_bundle() -> BundleActionSchema:
    return BundleActionSchema(
        agent_id="carol",
        num_required_signatures=3,
        compute_unit_limit=600_000,
        compute_unit_price_micro_lamports=2_000_000,
        collateral="USDC",
        amount=10_000,
        weights={"SOL": 0.5, "USDC": 0.5},
        side=Side.BUY,
    )


def _build_lp() -> LPActionSchema:
    return LPActionSchema(
        agent_id=7,
        num_required_signatures=1,
        compute_unit_limit=250_000,
        compute_unit_price_micro_lamports=500_000,
        collateral="USDC",
        amount=5_000,
        lp_type=LPActionType.WITHDRAW,
        target_weights={"SOL": 1, "USDC": 1},
        price_range=(0.9, 1.1),
        position_id="pos-1",
    )


def _build_order() -> OrderActionSchema:
    return OrderActionSchema(
        agent_id="dave",
        num_required_signatures=1,
        compute_unit_limit=120_000,
        compute_unit_price_micro_lamports=250_000,
        base="SOL",
        quote="USDC",
        side=OrderSide.SELL,
        price=125,
        quantity=20,
    )


@pytest.mark.parametrize(
    "build_schema, schema_cls, engine_cls",
    [
        (_build_swap, SwapActionSchema, SwapAction),
        (_build_single_asset, SingleAssetActionSchema, SingleAssetAction),
        (_build_bundle, BundleActionSchema, BundleAction),
        (_build_lp, LPActionSchema, LPAction),
        (_build_order, OrderActionSchema, OrderAction),
    ],
)
def test_action_schema_round_trips_through_engine_dataclass(
    build_schema, schema_cls, engine_cls,
):
    """Building via Pydantic, converting to the engine dataclass, then
    converting back, preserves CU fields and base ``Action`` fields."""
    schema = build_schema()
    engine = schema.to_engine()
    assert isinstance(engine, engine_cls)
    assert engine.agent_id == schema.agent_id
    assert engine.num_required_signatures == schema.num_required_signatures
    assert engine.compute_unit_limit == schema.compute_unit_limit
    assert (
        engine.compute_unit_price_micro_lamports
        == schema.compute_unit_price_micro_lamports
    )

    round_tripped = schema_cls.from_engine(engine)
    assert round_tripped.agent_id == schema.agent_id
    assert round_tripped.num_required_signatures == schema.num_required_signatures
    assert round_tripped.compute_unit_limit == schema.compute_unit_limit
    assert (
        round_tripped.compute_unit_price_micro_lamports
        == schema.compute_unit_price_micro_lamports
    )
