"""Integration tests for US-012 validator economics (PRD line 987).

Loads the Solana template through the live API, attaches a ``Validator``
agent with a matching leader schedule, submits a tip-bearing bundle, and
asserts that the per-validator and aggregate validator-revenue metrics
land in the run snapshot under ``metrics.validator_revenue``.
"""

from __future__ import annotations

import copy

from defi_sim.agents.validator import Validator, ValidatorParams
from defi_sim.core.types import SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.json import decode_jsonable
from defi_sim.engine.leader_schedule import LeaderSchedule
from defi_sim.engine.transactions import VersionedTransaction

import numpy as np
from defi_sim_api import state as sim_state
from defi_sim_api.backend.templates import experiment_templates


def test_full_run_emits_validator_revenue_metrics(client) -> None:
    template = next(
        t for t in experiment_templates() if t["template_id"] == "solana-sandwich-stress"
    )
    spec = copy.deepcopy(template["base_spec"])
    # Quiet competing agents so the bundle's pool lock is uncontested
    # (mirrors test_bundle_auction.py — same template + same isolation).
    # passive_lp now bootstrap-deposits on round 1; zero its deposit
    # fraction so it doesn't write-lock the pool the slot the bundle
    # expects to land in.
    for agent in spec["agents"]:
        params = agent.setdefault("params", {})
        if "frequency" in params:
            params["frequency"] = 0.0
        if agent["type"] == "manipulator":
            params["budget"] = 0
        if agent["type"] == "passive_lp":
            params["deposit_fraction"] = 0.0
    spec["num_rounds"] = 3
    spec["snapshot_interval"] = 1

    build_resp = client.post("/simulations/build", json=spec)
    assert build_resp.status_code == 201, build_resp.text
    sim_id = build_resp.json()["simulation_id"]

    entry = sim_state.get(sim_id)
    assert entry is not None
    engine = entry.engine
    execution = engine._execution_model
    assert execution.bundle_auction is not None

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_pool_address="lp-1",
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)
    # Manual agent injection requires seeding the per-agent RNG that
    # ``_run_round`` looks up before invoking ``decide``. The ``Validator``
    # agent's ``decide`` returns ``[]`` so the RNG is never consumed, but
    # the dict lookup still happens.
    engine._agent_rngs[validator.agent_id] = np.random.default_rng(0)
    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    tip_lamports = 10_000
    bundle = Bundle(
        txs=[
            VersionedTransaction(
                actions=[
                    SwapAction(
                        agent_id="sandwich-1",
                        token_in="USDC",
                        token_out="SOL",
                        amount_in=1_000,
                    )
                ]
            )
        ],
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

    step_resp = client.post(f"/simulations/{sim_id}/step")
    assert step_resp.status_code == 200, step_resp.text
    snapshot = step_resp.json()["snapshot"]

    bundle_outcomes = snapshot["bundle_outcomes"]
    assert len(bundle_outcomes) == 1
    assert bundle_outcomes[0]["status"] == "landed"
    assert bundle_outcomes[0]["tip_lamports"] == tip_lamports

    metrics = snapshot.get("metrics", {})
    assert "validator_revenue" in metrics, metrics

    revenue_by_epoch = decode_jsonable(metrics["validator_revenue"])
    assert revenue_by_epoch, revenue_by_epoch

    # One validator, one landed bundle -> exactly one (epoch, pubkey) entry.
    assert len(revenue_by_epoch) == 1
    epoch = next(iter(revenue_by_epoch))
    assert "val-pk-1" in revenue_by_epoch[epoch]
    entry_dict = revenue_by_epoch[epoch]["val-pk-1"]

    expected_pool = int(round(tip_lamports * 0.05))
    expected_validator = tip_lamports - expected_pool

    assert entry_dict["epoch"] == epoch
    assert entry_dict["pubkey"] == "val-pk-1"
    assert entry_dict["client"] == "jito_solana"
    assert entry_dict["validator_revenue_lamports"] == expected_validator
    assert entry_dict["stake_pool_revenue_lamports"] == expected_pool

    # Aggregate Jito-Solana validator MEV revenue per epoch (PRD line 971).
    aggregate_jito = sum(
        e["validator_revenue_lamports"]
        for e in revenue_by_epoch[epoch].values()
        if e["client"] == "jito_solana"
    )
    assert aggregate_jito == expected_validator

    # JitoSOL stake-pool inflow per epoch (PRD line 972).
    pool_inflow = sum(
        e["stake_pool_revenue_lamports"] for e in revenue_by_epoch[epoch].values()
    )
    assert pool_inflow == expected_pool
