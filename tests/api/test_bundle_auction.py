"""End-to-end bundle auction tests against the API surface.

PRD US-011 line 922: load the ``solana-sandwich-stress`` template, attach a
bundle to the live engine, run, and observe that the bundle landed in the
run snapshot's ``bundle_outcomes`` payload.
"""

from __future__ import annotations

import copy

from defi_sim.core.types import SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.transactions import VersionedTransaction
from defi_sim_api import state as sim_state
from defi_sim_api.backend.templates import experiment_templates


def test_solana_template_with_bundles_runs_end_to_end(client):
    """Solana template + a submitted bundle -> bundle lands in snapshot."""
    template = next(
        t for t in experiment_templates() if t["template_id"] == "solana-sandwich-stress"
    )
    spec = copy.deepcopy(template["base_spec"])
    # Quiet competing agents so the bundle's write-lock on the pool is
    # uncontested. Noise agents drop to frequency=0; the manipulator's
    # budget zeroes out so it emits no swaps. Passive LPs now bootstrap-
    # deposit on round 1 (the prior fee_yield gate deadlocked them);
    # zeroing ``deposit_fraction`` keeps them from minting and locking
    # the pool the same slot the test bundle expects to land in. The
    # PRD bullet just requires that the template loads and the bundle
    # lands; deterministic isolation prevents flakiness from in-template
    # actions colliding with the bundle's pool lock.
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
    execution = entry.engine._execution_model
    assert execution.bundle_auction is not None, "default bundle auction expected on solana_like"

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
                lamports=10_000,
                recipient="96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
            )
        ],
    )
    execution.submit_bundle(bundle)

    step_resp = client.post(f"/simulations/{sim_id}/step")
    assert step_resp.status_code == 200, step_resp.text
    snapshot = step_resp.json()["snapshot"]
    bundle_outcomes = snapshot["bundle_outcomes"]
    assert len(bundle_outcomes) == 1, bundle_outcomes
    outcome = bundle_outcomes[0]
    assert outcome["status"] == "landed", outcome
    assert outcome["tip_lamports"] == 10_000
    assert outcome["validator_revenue_lamports"] == 9_500
    assert outcome["stake_pool_revenue_lamports"] == 500
    assert outcome["num_txs"] == 1
