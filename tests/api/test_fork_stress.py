"""Integration test for US-014 fork stress (PRD line 1148).

Loads the ``solana-sandwich-stress`` template, configures a non-zero
``fork_probability_per_slot``, attaches a Validator + per-slot bundles,
runs 200 slots, and asserts:

  * fork events are emitted (>=1) and their count is consistent with the
    configured Bernoulli probability,
  * at least one selected bundle is reverted by a fork hit,
  * tip-revenue accounting is consistent: no validator credit accrues
    for a bundle marked ``reverted`` (current-slot tip-revert from iter
    64), and aggregate validator revenue cannot exceed the sum of
    landed paid tips × (1 - stake_pool_share). With past-slot revert
    via snapshot/restore of ``_validator_revenue_by_epoch``, a fork
    can also retroactively wipe credits from earlier slots in the
    abandoned range, so the relation is ``<=`` rather than ``==``.
"""

from __future__ import annotations

import copy
import random
from collections import deque

import numpy as np

from defi_sim.agents.validator import Validator, ValidatorParams
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS, Bundle, TipPayment
from defi_sim.engine.events import EventType
from defi_sim.engine.fork import ChainReorgForkSpec
from defi_sim.engine.leader_schedule import LeaderSchedule
from defi_sim.engine.transactions import VersionedTransaction
from defi_sim_api.backend.templates import experiment_templates


def test_high_fork_probability_run_emits_fork_events_and_reverts_tips() -> None:
    template = next(
        t
        for t in experiment_templates()
        if t["template_id"] == "solana-sandwich-stress"
    )
    spec = copy.deepcopy(template["base_spec"])
    for agent in spec["agents"]:
        params = agent.setdefault("params", {})
        if "frequency" in params:
            params["frequency"] = 0.0
        if agent["type"] == "manipulator":
            params["budget"] = 0
    n_slots = 200
    spec["num_rounds"] = n_slots
    spec["snapshot_interval"] = n_slots

    engine = build_engine(spec)
    execution = engine._execution_model
    assert execution.bundle_auction is not None

    fork_prob = 0.05
    max_depth = 5
    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=fork_prob,
        max_reorg_depth_slots=max_depth,
        seed=42,
    )
    execution._fork_spec = fork_spec
    execution._fork_rng = random.Random(fork_spec.seed)
    execution._slot_history = deque(maxlen=fork_spec.max_reorg_depth_slots + 1)

    validator = Validator(
        "validator-fork-1",
        ValidatorParams(
            pubkey="val-fork-pk",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_pool_address=None,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)
    engine._agent_rngs[validator.agent_id] = np.random.default_rng(0)
    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    fork_events: list = []
    engine._bus.on(
        EventType.FORK_REORG,
        lambda event: fork_events.append(event),
    )

    bundles_selected = 0
    bundles_reverted = 0
    reverted_paid_tip_total = 0
    landed_paid_tip_total = 0
    tip_lamports = MIN_BUNDLE_TIP_LAMPORTS * 100

    for _slot in range(n_slots):
        bundle = Bundle(
            txs=[VersionedTransaction(actions=[])],
            tip_payments=[
                TipPayment(
                    tx_index=0,
                    location="standalone_tx",
                    lamports=tip_lamports,
                    recipient="96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
                )
            ],
        )
        execution.submit_bundle(bundle)
        engine.step()
        for _selected, result in execution._last_slot_selected_bundles:
            bundles_selected += 1
            if result.reverted:
                bundles_reverted += 1
                reverted_paid_tip_total += sum(
                    tp.lamports for tp in result.paid_tips
                )
            else:
                landed_paid_tip_total += sum(
                    tp.lamports for tp in result.paid_tips
                )

    assert len(fork_events) > 0, (
        f"expected >=1 FORK_REORG events over {n_slots} slots at prob={fork_prob}; got 0"
    )
    expected_events = n_slots * fork_prob
    sigma = (n_slots * fork_prob * (1.0 - fork_prob)) ** 0.5
    assert abs(len(fork_events) - expected_events) <= 5 * sigma, (
        f"observed {len(fork_events)} fork events; expected ≈ {expected_events} ± {5 * sigma:.2f}"
    )

    assert bundles_selected > 0, "expected >=1 selected bundle over 200 slots"
    assert bundles_reverted > 0, (
        f"expected >=1 reverted bundle given {len(fork_events)} fork events"
    )

    assert reverted_paid_tip_total == 0, (
        f"reverted bundles must clear paid_tips (got {reverted_paid_tip_total} lamports)"
    )

    revenue_by_epoch = engine.validator_revenue_by_epoch
    aggregate_validator_revenue = sum(
        entry.validator_revenue_lamports
        for bucket in revenue_by_epoch.values()
        for entry in bucket.values()
    )
    upper_bound_validator_revenue = int(
        round(landed_paid_tip_total * (1 - validator.params.stake_pool_share))
    )
    assert 0 <= aggregate_validator_revenue <= upper_bound_validator_revenue, (
        f"validator revenue {aggregate_validator_revenue} not in "
        f"[0, {upper_bound_validator_revenue}] from {landed_paid_tip_total} "
        f"landed-tip lamports"
    )
    assert aggregate_validator_revenue < upper_bound_validator_revenue, (
        f"expected past-slot revert to wipe at least one credit; "
        f"revenue {aggregate_validator_revenue} == upper bound "
        f"{upper_bound_validator_revenue}"
    )
