"""Atomic state rollback tests for ``atomic_state_boundary`` (PRD US-005).

PRD line 417: ``test_existing_snapshot_then_mutate_then_restore_yields_original``
asserts the existing ``_snapshot_bundle_mutable_state`` /
``_restore_bundle_mutable_state`` primitives form a faithful round-trip:
take a snapshot, mutate every state surface the snapshot covers, restore,
and observe the pre-mutation state byte-for-byte. This is the basic
correctness guarantee the BundleAuction revert path (1.7 step 4) relies on.
"""

from __future__ import annotations

import copy

import pytest

from defi_sim.engine.api import build_engine


SOLANA_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "noise-1",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
        {
            "type": "noise",
            "agent_id": "noise-2",
            "params": {"collateral": "USDC", "frequency": 1.0},
            "initial_balances": {"USDC": 1_000_000_000},
        },
    ],
    "num_rounds": 1,
    "seed": 7,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
    },
}


def test_existing_snapshot_then_mutate_then_restore_yields_original() -> None:
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    pre_agent_states = {a.agent_id: copy.deepcopy(a.state) for a in engine._agents}
    pre_market = engine._market.copy()
    pre_fee_dest = copy.deepcopy(engine._fee_destination_balances)
    pre_pending_lp = copy.deepcopy(engine._pending_lp_fees)
    pre_round_fee_splits = copy.deepcopy(engine._round_fee_splits)
    pre_last_feed = copy.deepcopy(engine._last_feed_prices)
    pre_round_feed = copy.deepcopy(engine._round_feed_prices)
    pre_rng = {
        "agent": copy.deepcopy(engine._agent_rng.bit_generator.state),
        "ordering": copy.deepcopy(engine._ordering_rng.bit_generator.state),
        "feed": copy.deepcopy(engine._feed_rng.bit_generator.state),
        "engine": copy.deepcopy(engine._engine_rng.bit_generator.state),
        "submission": copy.deepcopy(engine._submission_rng.bit_generator.state),
    }

    snap = engine._snapshot_bundle_mutable_state()

    for agent in engine._agents:
        for token in list(agent.state.balances.keys()):
            agent.state.balances[token] = 0
        agent.state.cumulative_volume = 999_999
        agent.state.realized_pnl = -777
    market_reserves = engine._market._reserves  # type: ignore[attr-defined]
    if market_reserves:
        first_token = next(iter(market_reserves.keys()))
        market_reserves[first_token] = 1
    engine._fee_destination_balances["mutated"] = {"USDC": 12345}
    engine._pending_lp_fees[id(engine._market)] = {"USDC": 67890}
    engine._round_fee_splits["mutated"] = {"USDC": 11111}
    engine._last_feed_prices = {"mutated_feed": 9999.0}
    engine._round_feed_prices = {"mutated_feed": 8888.0}
    for _ in range(50):
        engine._agent_rng.random()
        engine._ordering_rng.random()
        engine._feed_rng.random()
        engine._engine_rng.random()
        engine._submission_rng.random()

    engine._restore_bundle_mutable_state(snap)

    for agent in engine._agents:
        assert agent.state.balances == pre_agent_states[agent.agent_id].balances
        assert agent.state.cumulative_volume == pre_agent_states[agent.agent_id].cumulative_volume
        assert agent.state.realized_pnl == pre_agent_states[agent.agent_id].realized_pnl

    assert engine._market._reserves == pre_market._reserves  # type: ignore[attr-defined]

    assert engine._fee_destination_balances == pre_fee_dest
    assert engine._pending_lp_fees == pre_pending_lp
    assert engine._round_fee_splits == pre_round_fee_splits
    assert engine._last_feed_prices == pre_last_feed
    assert engine._round_feed_prices == pre_round_feed

    assert engine._agent_rng.bit_generator.state == pre_rng["agent"]
    assert engine._ordering_rng.bit_generator.state == pre_rng["ordering"]
    assert engine._feed_rng.bit_generator.state == pre_rng["feed"]
    assert engine._engine_rng.bit_generator.state == pre_rng["engine"]
    assert engine._submission_rng.bit_generator.state == pre_rng["submission"]


def test_atomic_boundary_reverts_market_agent_fee_and_oracle_state() -> None:
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    pre_agent_states = {a.agent_id: copy.deepcopy(a.state) for a in engine._agents}
    pre_market_reserves = copy.deepcopy(engine._market._reserves)  # type: ignore[attr-defined]
    pre_fee_dest = copy.deepcopy(engine._fee_destination_balances)
    pre_pending_lp = copy.deepcopy(engine._pending_lp_fees)
    pre_round_fee_splits = copy.deepcopy(engine._round_fee_splits)
    pre_last_feed = copy.deepcopy(engine._last_feed_prices)
    pre_round_feed = copy.deepcopy(engine._round_feed_prices)

    with engine.atomic_state_boundary() as boundary:
        for agent in engine._agents:
            for token in list(agent.state.balances.keys()):
                agent.state.balances[token] = 0
            agent.state.cumulative_volume = 999_999
            agent.state.realized_pnl = -777
        market_reserves = engine._market._reserves  # type: ignore[attr-defined]
        if market_reserves:
            first_token = next(iter(market_reserves.keys()))
            market_reserves[first_token] = 1
        engine._fee_destination_balances["mutated"] = {"USDC": 12345}
        engine._pending_lp_fees[id(engine._market)] = {"USDC": 67890}
        engine._round_fee_splits["mutated"] = {"USDC": 11111}
        engine._last_feed_prices = {"mutated_feed": 9999.0}
        engine._round_feed_prices = {"mutated_feed": 8888.0}
        boundary.rollback()

    for agent in engine._agents:
        assert agent.state.balances == pre_agent_states[agent.agent_id].balances
        assert agent.state.cumulative_volume == pre_agent_states[agent.agent_id].cumulative_volume
        assert agent.state.realized_pnl == pre_agent_states[agent.agent_id].realized_pnl

    assert engine._market._reserves == pre_market_reserves  # type: ignore[attr-defined]
    assert engine._fee_destination_balances == pre_fee_dest
    assert engine._pending_lp_fees == pre_pending_lp
    assert engine._round_fee_splits == pre_round_fee_splits
    assert engine._last_feed_prices == pre_last_feed
    assert engine._round_feed_prices == pre_round_feed


def test_atomic_boundary_reverts_priority_fee_market_and_validator_tip_state() -> None:
    """PRD US-005 line 419: ``atomic_state_boundary`` must roll back priority
    fee market observations (US-010) and the bundle tip ledger / validator
    tip revenue mirror (US-011) so reverted slots' state never leaks past
    the boundary.
    """
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    pfm = engine.priority_fee_market
    assert pfm is not None, (
        "Solana-shaped execution model must expose priority_fee_market"
    )

    with engine.atomic_state_boundary() as boundary:
        pfm.observe("mutated_account", 0, 999_999)
        engine._tip_outcomes.append({"mutated": True})
        engine._validator_revenue_by_epoch.setdefault(0, {})["mutated_validator"] = 1
        boundary.rollback()

    # Priority fee market observations / EWMA baselines reverted.
    assert "mutated_account" not in pfm._observations
    assert "mutated_account" not in pfm._ewma_baseline
    assert pfm.previous_percentiles("mutated_account") is None

    # Bundle tip ledger reverted.
    assert {"mutated": True} not in engine._tip_outcomes

    # Per-(epoch, validator) revenue mirror reverted.
    assert "mutated_validator" not in engine._validator_revenue_by_epoch.get(0, {})


def test_snapshot_serialization_round_trips() -> None:
    """PRD line 420: only required if atomic_state_boundary snapshots cross
    PRs / IPC; otherwise skip. The current implementation
    (``_snapshot_bundle_mutable_state``) returns an in-memory dict produced via
    ``copy.deepcopy``; nothing is serialized to bytes or persisted across
    process boundaries. ``engine/snapshots.py`` already covers full-engine
    msgpack round-trips for the separate "save/restore a run" use case
    (see ``tests/engine/test_snapshot_round_trip.py``).

    This test introspects the bundle-local snapshot for a serialization API
    (``to_bytes`` / ``from_bytes`` or msgpack-encodable mapping with
    ``serialize_bundle_snapshot`` helper). If absent, it skips with rationale.
    Once the bundle-snapshot format gains an IPC story, replace the skip with
    real round-trip assertions.
    """
    engine = build_engine(copy.deepcopy(SOLANA_SPEC))
    engine.run()

    snap = engine._snapshot_bundle_mutable_state()

    has_serialize_helper = hasattr(engine, "serialize_bundle_snapshot") or hasattr(
        engine, "_serialize_bundle_snapshot"
    )
    has_dunder_serialize = hasattr(snap, "to_bytes") and hasattr(
        type(snap), "from_bytes"
    )
    if not has_serialize_helper and not has_dunder_serialize:
        pytest.skip(
            "Bundle-local atomic_state_boundary snapshots are in-memory only "
            "(no IPC / cross-PR serialization story). PRD US-005 line 420 "
            "explicitly allows skipping in that case. Full-engine snapshot "
            "round-trips are covered by tests/engine/test_snapshot_round_trip.py."
        )

    if has_dunder_serialize:
        encoded = snap.to_bytes()  # type: ignore[union-attr]
        decoded = type(snap).from_bytes(encoded)  # type: ignore[union-attr]
    else:
        serialize = getattr(engine, "serialize_bundle_snapshot", None) or getattr(
            engine, "_serialize_bundle_snapshot"
        )
        deserialize = getattr(
            engine, "deserialize_bundle_snapshot", None
        ) or getattr(engine, "_deserialize_bundle_snapshot")
        encoded = serialize(snap)  # type: ignore[misc]
        decoded = deserialize(encoded)  # type: ignore[misc]

    engine._restore_bundle_mutable_state(decoded)
