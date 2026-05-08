"""Tests for the ``OracleSource`` ABC and feed → oracle projection.

PRD US-006 step 1.8b complete: the ``PriceFeed`` ABC and the
``LegacyFeedAsOracle`` shim are gone. Multi-token feed aggregators
project per-token ``OracleSource`` views via ``oracle_for(token)``.
"""

from __future__ import annotations

import numpy as np
import pytest

from defi_sim.engine.feeds import HistoricalFeed
from defi_sim.engine.gas import ComputeUnitCost
from defi_sim.engine.oracles import (
    OracleSlotCost,
    OracleSource,
    OracleUpdateAction,
    PullOracle,
    PushOracle,
    oracle_costs_per_slot,
    passes_confidence_gate,
    pyth_lazer_solusdc,
    pyth_pull_solusdc,
    switchboard_on_demand_solusdc,
)


def test_oracle_source_is_abstract():
    with pytest.raises(TypeError):
        OracleSource()  # type: ignore[abstract]


def test_historical_feed_oracle_for_returns_per_token_oracle_source():
    feed = HistoricalFeed({"SOL": np.array([100, 101, 102, 103], dtype=np.int64)})
    oracle = feed.oracle_for("SOL")

    assert isinstance(oracle, OracleSource)
    assert oracle.update_mode == "push"
    assert oracle.confidence_interval == 0.0

    price, conf = oracle.price_at(slot=2)
    assert price == 102
    assert conf == 0.0


# Step 1.8b lock-tests (``test_pricefeed_class_does_not_exist`` and
# friends) live in ``tests/engine/test_no_legacy_pricefeed.py`` per PRD
# US-006 line 458.


def test_push_oracle_updates_at_configured_cadence():
    """PRD line 500: cadence=10 over 100 slots → 10 distinct update slots."""
    truth = lambda slot: 100 + slot  # noqa: E731 — test-local stub
    oracle = PushOracle(
        update_cadence_slots=10,
        update_cost_lamports=5_000,
        price_source=truth,
    )

    assert oracle.update_mode == "push"
    assert oracle.update_cadence_slots == 10
    assert oracle.update_cost_lamports == 5_000

    seen_update_slots = {oracle.last_update_slot(s) for s in range(100)}
    assert seen_update_slots == {0, 10, 20, 30, 40, 50, 60, 70, 80, 90}

    # The price observed at slot 9 is the price published at slot 0
    # (i.e. truth(0)), not truth(9): the aggregator hasn't republished.
    price_at_9, _ = oracle.price_at(9)
    assert price_at_9 == truth(0)
    price_at_10, _ = oracle.price_at(10)
    assert price_at_10 == truth(10)


def test_push_oracle_staleness_grows_between_updates():
    """PRD line 500: max staleness = cadence - 1 = 9 for cadence=10."""
    oracle = PushOracle(
        update_cadence_slots=10,
        update_cost_lamports=5_000,
        price_source=lambda _slot: 100,
    )

    staleness_within_first_window = [oracle.staleness(s) for s in range(10)]
    assert staleness_within_first_window == list(range(10))
    assert max(staleness_within_first_window) == oracle.update_cadence_slots - 1

    # Resets at the next update boundary.
    assert oracle.staleness(10) == 0
    assert oracle.staleness(19) == 9
    assert oracle.staleness(20) == 0


def test_pull_oracle_requires_explicit_pull_to_advance():
    """PRD line 507: cached price stays frozen between consumer pulls.

    A pull-mode oracle (Pyth Pull, Switchboard On-Demand) only advances
    its cached price when the consumer includes the price-update
    instruction. Slots can pass without the cached value changing.
    """
    truth = lambda slot: 100 + slot  # noqa: E731 — test-local stub
    oracle = PullOracle(
        oracle_id="SOL/USD",
        update_cu_cost=15_000,
        update_lamport_cost=5_000,
        staleness_tolerance_slots=10,
        price_source=truth,
    )

    assert oracle.update_mode == "pull"
    assert oracle.last_pull_slot() is None
    assert oracle.is_stale(0) is True

    # Without an explicit pull there is no cached price to read.
    with pytest.raises(RuntimeError):
        oracle.price_at(0)

    oracle.pull(0)
    assert oracle.last_pull_slot() == 0
    cached_price, _ = oracle.price_at(0)
    assert cached_price == truth(0)

    # Time advances but no consumer pulled — price stays at the slot-0 value.
    cached_price_at_5, _ = oracle.price_at(5)
    assert cached_price_at_5 == truth(0)
    assert oracle.staleness(5) == 5

    # Now a consumer pulls; the cache moves forward.
    oracle.pull(5)
    cached_price_at_5_after_pull, _ = oracle.price_at(5)
    assert cached_price_at_5_after_pull == truth(5)
    assert oracle.staleness(5) == 0


def test_pull_oracle_emits_oracle_update_action_when_stale():
    """PRD line 508: ``pull(slot)`` returns a fee-bearing ``OracleUpdateAction``.

    The action carries the oracle id, target slot, and the CU cost the
    update instruction consumes; consumers attach this to their tx so
    the standard ``ComputeUnitCost`` model charges them for the refresh.
    """
    oracle = PullOracle(
        oracle_id="SOL/USD",
        update_cu_cost=15_000,
        update_lamport_cost=5_000,
        staleness_tolerance_slots=3,
        price_source=lambda _slot: 100,
    )
    oracle.pull(0)

    # Within tolerance: not stale.
    assert oracle.is_stale(2) is False
    assert oracle.is_stale(3) is False
    # Strictly outside tolerance: stale, consumer must re-pull.
    assert oracle.is_stale(4) is True

    action = oracle.pull(7, agent_id="liquidator-1")
    assert isinstance(action, OracleUpdateAction)
    assert action.agent_id == "liquidator-1"
    assert action.oracle_id == "SOL/USD"
    assert action.target_slot == 7
    assert action.compute_unit_limit == 15_000
    # Pulling clears the staleness flag at the pull slot.
    assert oracle.is_stale(7) is False


def test_pyth_pull_solusdc_preset_returns_calibrated_pulloracle():
    """PRD line 484: Pyth Pull preset is a one-call PullOracle constructor."""
    truth = lambda slot: 100 + slot  # noqa: E731
    oracle = pyth_pull_solusdc(price_source=truth)

    assert isinstance(oracle, PullOracle)
    assert oracle.update_mode == "pull"
    assert oracle.oracle_id == "pyth_pull_sol_usdc"
    assert oracle.update_cu_cost > 0
    assert oracle.update_lamport_cost >= 0
    assert oracle.staleness_tolerance_slots > 0
    assert oracle.confidence_interval > 0


def test_pyth_lazer_solusdc_preset_has_tighter_freshness_than_pull():
    """Pyth Lazer is the sub-slot variant; tolerance must be lower."""
    truth = lambda _slot: 100  # noqa: E731
    pull = pyth_pull_solusdc(price_source=truth)
    lazer = pyth_lazer_solusdc(price_source=truth)

    assert isinstance(lazer, PullOracle)
    assert lazer.oracle_id == "pyth_lazer_sol_usdc"
    assert lazer.staleness_tolerance_slots < pull.staleness_tolerance_slots
    assert lazer.confidence_interval <= pull.confidence_interval


def test_switchboard_on_demand_solusdc_preset_returns_pulloracle():
    """Switchboard On-Demand is the third pull-mode 2026 preset."""
    truth = lambda _slot: 100  # noqa: E731
    oracle = switchboard_on_demand_solusdc(price_source=truth)

    assert isinstance(oracle, PullOracle)
    assert oracle.update_mode == "pull"
    assert oracle.oracle_id == "switchboard_on_demand_sol_usdc"
    assert oracle.update_cu_cost > 0


def test_oracle_presets_accept_initial_pull_slot_for_prewarm():
    """Presets pass through ``initial_pull_slot`` so callers can pre-warm."""
    truth = lambda slot: 100 + slot  # noqa: E731
    oracle = pyth_pull_solusdc(price_source=truth, initial_pull_slot=0)

    assert oracle.last_pull_slot() == 0
    price, _ = oracle.price_at(0)
    assert price == 100


def test_pull_oracle_cost_charged_to_consumer():
    """PRD line 509: consumer's tx cost includes the oracle pull cost.

    Two halves of the cost surface, both borne by the consumer when they
    include an ``OracleUpdateAction`` in their tx (PRD line 471-472):

    * **CU half** — ``update_cu_cost`` is stamped onto the action's
      ``compute_unit_limit`` by ``PullOracle.pull``. When the action runs
      through ``ComputeUnitCost.breakdown`` with a non-zero
      ``compute_unit_price_micro_lamports``, the priority-fee lamports
      scale with the oracle's CU cost (i.e. the consumer pays the
      validator more for including the update instruction).
    * **Lamport half** — ``update_lamport_cost`` is the flat consumer-paid
      tx-fee surcharge surfaced through the ``oracle_costs_per_slot``
      aggregator (consumer-paid ``lamports`` field, distinct from the
      operator-paid ``operator_lamports`` used for push oracles).
    """
    oracle = PullOracle(
        oracle_id="SOL/USD",
        update_cu_cost=15_000,
        update_lamport_cost=2_500,
        staleness_tolerance_slots=10,
        price_source=lambda _slot: 100,
    )

    # Consumer pulls; receives the OracleUpdateAction to bundle into their tx.
    consumer_id = "consumer-1"
    update_action = oracle.pull(7, agent_id=consumer_id)
    update_action.compute_unit_price_micro_lamports = 1_000_000  # 1 lamport / CU

    # CU half: the priority fee charged on the update instruction matches
    # ceil(price_micro * cu_limit / 1_000_000) = 15_000 lamports.
    cost_model = ComputeUnitCost()
    breakdown = cost_model.breakdown(update_action, round=7)
    assert breakdown.cu_limit_source == "explicit"
    assert breakdown.priority_fee_lamports == 15_000
    # Same agent (consumer) is on the hook for both base and priority components.
    assert update_action.agent_id == consumer_id
    assert breakdown.total_lamports == breakdown.base_fee_lamports + 15_000

    # Lamport half: the per-slot aggregator charges the consumer-paid
    # update_lamport_cost on each pulled slot, not operator_lamports.
    costs = oracle_costs_per_slot(
        pull_oracle_pulls={"SOL/USD": [7]},
        pull_oracles={"SOL/USD": oracle},
    )
    assert costs == [
        OracleSlotCost(
            slot=7,
            cu=15_000,
            lamports=2_500,
            operator_lamports=0,
        )
    ]


def test_confidence_gate_blocks_liquidation_in_band():
    """PRD line 502: price=100, confidence=10, threshold=95 → False.

    The lower edge of the confidence band (100-10=90) is below the
    threshold, so a liquidator cannot prove the position is undercollateralized.
    """
    assert passes_confidence_gate(price=100, confidence=10, threshold=95) is False


def test_confidence_gate_allows_liquidation_outside_band():
    """PRD line 502: price=100, confidence=2, threshold=95 → True.

    100-2=98 is strictly above 95, so the entire confidence band is above
    the threshold and liquidation is safe.
    """
    assert passes_confidence_gate(price=100, confidence=2, threshold=95) is True


# ---------------------------------------------------------------------------
# US-006 line 497: oracle_preset wiring through ``build_engine``
# ---------------------------------------------------------------------------


def _solana_spec_with_feed(oracle_preset: str | None = None) -> dict:
    """Minimal Solana ``RunSpec`` with a SOL feed, optionally selecting a
    builder oracle preset via ``execution.params.oracle_preset``."""
    spec: dict = {
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
                "agent_id": "n0",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000, "SOL": 1_000_000},
            },
        ],
        "feeds": [
            {"type": "historical", "params": {"prices": {"SOL": [120, 121, 122]}}},
        ],
        "num_rounds": 1,
        "seed": 7,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
            "params": {},
        },
    }
    if oracle_preset is not None:
        spec["execution"]["params"]["oracle_preset"] = oracle_preset
    return spec


def test_build_engine_registers_pyth_pull_preset_from_spec():
    """PRD US-006 line 497: ``execution.params.oracle_preset = "pyth_pull"``
    causes ``build_engine`` to register a ``PullOracle`` keyed by the preset's
    ``oracle_id`` and pre-warm it at slot 0 from the SOL feed."""
    from defi_sim.engine.api import build_engine

    engine = build_engine(_solana_spec_with_feed("pyth_pull"))

    pull_oracles = engine._pull_oracles
    assert "pyth_pull_sol_usdc" in pull_oracles
    oracle = pull_oracles["pyth_pull_sol_usdc"]
    assert isinstance(oracle, PullOracle)
    # Pre-warmed at slot 0 ⇒ truth feed value 120 is observable without a pull.
    price, _ = oracle.price_at(0)
    assert price == 120


def test_build_engine_registers_pyth_lazer_preset_from_spec():
    """``pyth_lazer`` and ``switchboard_on_demand`` route to their own oracle
    ids so downstream telemetry can disambiguate which preset a run used."""
    from defi_sim.engine.api import build_engine

    lazer = build_engine(_solana_spec_with_feed("pyth_lazer"))
    sb = build_engine(_solana_spec_with_feed("switchboard_on_demand"))

    assert "pyth_lazer_sol_usdc" in lazer._pull_oracles
    assert "switchboard_on_demand_sol_usdc" in sb._pull_oracles


def test_build_engine_skips_oracle_preset_when_none_or_missing():
    """Builder must not register an oracle when the preset is ``"none"`` or
    omitted — chain-neutral and pre-pivot specs stay untouched."""
    from defi_sim.engine.api import build_engine

    none_engine = build_engine(_solana_spec_with_feed("none"))
    missing_engine = build_engine(_solana_spec_with_feed(None))

    assert none_engine._pull_oracles == {}
    assert missing_engine._pull_oracles == {}


def test_build_engine_rejects_unknown_oracle_preset():
    """Unknown preset names fail loudly so a typo in the builder field can't
    silently disable the feature."""
    from defi_sim.engine.api import build_engine

    with pytest.raises(ValueError, match="unknown oracle_preset"):
        build_engine(_solana_spec_with_feed("chainlink_v3"))


def test_build_engine_rejects_oracle_preset_without_sol_feed():
    """A preset that has no SOL feed to source truth from is rejected at
    build time rather than silently no-opping inside the engine."""
    from defi_sim.engine.api import build_engine

    spec = _solana_spec_with_feed("pyth_pull")
    spec["feeds"] = []  # remove the SOL feed

    with pytest.raises(ValueError, match="requires a price feed"):
        build_engine(spec)
