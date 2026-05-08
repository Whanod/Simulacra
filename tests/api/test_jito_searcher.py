"""Integration test for US-013 ``JitoSearcher`` (PRD line 1070).

Loads the ``solana-sandwich-stress`` template, attaches a ``JitoSearcher``
agent, and over 1000 slots submits a bundle per slot mimicking the wired
searcher's behavior. Asserts that the searcher's recorded bundle-landing
rate is non-zero and consistent with the configured
``jito_relayer_landing_prob_baseline`` prior (PRD US-004 line 1.5).

The searcher's ``decide()`` is currently a no-op; bundles flow through
``execution.submit_bundle`` directly here. As the agent-engine bundle ABI
firms up, this hand-wired plumbing collapses into ``decide()``.
"""

from __future__ import annotations

import copy
import math

import numpy as np

from defi_sim.agents.jito_searcher import JitoSearcher, JitoSearcherParams
from defi_sim.agents.tip_curve import TipCurveSpec
from defi_sim.engine.api import build_engine
from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS, Bundle, TipPayment
from defi_sim.engine.events import EventType
from defi_sim.engine.submission_priors import SubmissionPathPriors
from defi_sim.engine.transactions import VersionedTransaction
from defi_sim_api.backend.templates import experiment_templates


def test_full_run_with_searcher_lands_some_bundles() -> None:
    template = next(
        t
        for t in experiment_templates()
        if t["template_id"] == "solana-sandwich-stress"
    )
    spec = copy.deepcopy(template["base_spec"])
    # Quiet competing flow so the searcher's bundle is the only contention
    # (noise traders / manipulator can otherwise contend for the pool's
    # write lock and skew observed landing rate).
    for agent in spec["agents"]:
        params = agent.setdefault("params", {})
        if "frequency" in params:
            params["frequency"] = 0.0
        if agent["type"] == "manipulator":
            params["budget"] = 0
    n_slots = 1_000
    spec["num_rounds"] = n_slots
    spec["snapshot_interval"] = n_slots

    engine = build_engine(spec)
    execution = engine._execution_model
    assert execution.bundle_auction is not None

    landing_prob = 0.5
    execution._submission_priors = SubmissionPathPriors(
        jito_relayer_landing_prob_baseline=landing_prob
    )
    execution._submission_rng = np.random.default_rng(seed=99)

    searcher = JitoSearcher(
        agent_id="searcher-1",
        params=JitoSearcherParams(
            strategies=["backrun"],
            tip_curve=TipCurveSpec(kind="linear"),
            min_ev_to_submit_lamports=10_000,
            tip_account="tip-account",
        ),
    )
    engine._agents.append(searcher)
    engine._agent_rngs[searcher.agent_id] = np.random.default_rng(0)

    for _ in range(n_slots):
        bundle = Bundle(
            txs=[VersionedTransaction(actions=[])],
            tip_payments=[
                TipPayment(
                    tx_index=0,
                    location="standalone_tx",
                    lamports=MIN_BUNDLE_TIP_LAMPORTS,
                    recipient="tip-account",
                )
            ],
        )
        execution.submit_bundle(bundle)
        searcher.metrics.record_submitted("backrun", MIN_BUNDLE_TIP_LAMPORTS)
        engine.step()
        if any(
            selected is bundle
            for selected, _outcome in execution._last_slot_selected_bundles
        ):
            searcher.metrics.record_landed(
                "backrun",
                tip_lamports=MIN_BUNDLE_TIP_LAMPORTS,
                realized_ev_lamports=0,
            )

    counters = searcher.metrics.by_strategy["backrun"]
    assert counters.bundles_submitted == n_slots
    assert counters.bundles_landed > 0, (
        "expected non-zero landing rate over 1000 slots with prior=0.5"
    )

    observed_rate = searcher.metrics.landing_rate("backrun")
    sigma = math.sqrt(landing_prob * (1.0 - landing_prob) / n_slots)
    assert abs(observed_rate - landing_prob) <= 3 * sigma, (
        f"observed rate={observed_rate:.4f} expected≈{landing_prob} "
        f"±{3 * sigma:.4f} (3σ over {n_slots} slots)"
    )


_VICTIM_SPEC: dict = {
    "market": {
        "type": "cfamm",
        "tokens": [
            {"id": "SOL", "symbol": "SOL", "decimals": 9, "native": True, "standard": "native"},
            {"id": "USDC", "symbol": "USDC", "decimals": 6, "standard": "spl"},
        ],
        "params": {
            "initial_liquidity": 1_000_000_000,
            "collateral_token": "USDC",
        },
    },
    "agents": [
        {
            "type": "noise",
            "agent_id": "victim",
            "params": {"collateral": "USDC", "frequency": 0.0},
            "initial_balances": {"USDC": 1_000_000_000_000, "SOL": 1_000_000_000_000},
        },
    ],
    "num_rounds": 50,
    "seed": 11,
    "execution": {
        "type": "solana_like",
        "ordering": {"type": "priority"},
        "gas_model": {"type": "compute_unit"},
        "params": {"visible_roles": ["jito_searcher"], "cost_token": "USDC"},
    },
    "bundle_auction": {"jito_stake_pool_share": 0.05},
}


def test_searcher_decide_path_lands_backrun_alongside_victim() -> None:
    """Production path: ``JitoSearcher.decide()`` builds a back-run bundle
    from a real victim swap in ``ctx.pending_actions`` and the bundle lands
    despite the victim still being in the regular queue. Validates fixes
    for the bundle/queue self-conflict (#1) and landing attribution (#2).
    """
    from defi_sim.core.agent import Agent
    from defi_sim.core.types import AgentRole, AgentState, SwapAction

    class _VictimEmitter(Agent):
        def __init__(self) -> None:
            self.agent_id = "victim"
            self.state = AgentState(agent_id="victim", role=AgentRole("noise"))

        def decide(self, ctx):  # type: ignore[no-untyped-def]
            return [SwapAction(
                agent_id="victim",
                token_in="USDC",
                token_out="SOL",
                amount_in=10_000,
            )]

    spec = copy.deepcopy(_VICTIM_SPEC)
    engine = build_engine(spec)
    execution = engine._execution_model
    assert execution.bundle_auction is not None
    # Lock landing prob to 1.0 so we directly test the auction's coexistence
    # exemption rather than Bernoulli sampling.
    execution._submission_priors = SubmissionPathPriors(
        jito_relayer_landing_prob_baseline=1.0
    )
    execution._submission_rng = np.random.default_rng(seed=7)

    # Replace the placeholder noise agent with a deterministic victim emitter
    # so each slot has exactly one large SwapAction.
    engine._agents = [a for a in engine._agents if a.agent_id != "victim"]
    victim = _VictimEmitter()
    victim.state.balances = {"USDC": 1_000_000_000_000, "SOL": 1_000_000_000_000}
    engine._agents.append(victim)
    engine._agent_rngs[victim.agent_id] = np.random.default_rng(0)

    searcher = JitoSearcher(
        agent_id="searcher-prod",
        params=JitoSearcherParams(
            strategies=["backrun"],
            tip_curve=TipCurveSpec(kind="linear"),
            min_ev_to_submit_lamports=1_000,
            tip_account="96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        ),
    )
    # Searcher needs inventory on both sides to fund the back-run swap and
    # the tip; JitoSearcher.__init__ doesn't seed balances on its own.
    searcher.state.balances = {"SOL": 1_000_000_000_000, "USDC": 1_000_000_000_000}
    engine._agents.append(searcher)
    engine._agent_rngs[searcher.agent_id] = np.random.default_rng(0)

    executed_actions = []
    engine._bus.on(
        EventType.ACTION_EXECUTED,
        lambda event: executed_actions.append(event.data.get("action")),
    )
    engine.step()
    swaps = [a for a in executed_actions if isinstance(a, SwapAction)]
    assert len(swaps) >= 2
    assert swaps[0].agent_id == "victim"
    assert swaps[1].agent_id == "searcher-prod"

    for _ in range(19):
        engine.step()

    counters = searcher.metrics.by_strategy.get("backrun")
    assert counters is not None, "searcher.decide() never submitted a bundle"
    assert counters.bundles_submitted > 0
    # The key assertion: bundles landed. Before the fix, all backrun
    # bundles were dropped with bundle_lock_conflict against the victim's
    # write on the same pool — landing was identically zero.
    assert counters.bundles_landed > 0, (
        f"expected at least one backrun to land via decide() path; "
        f"submitted={counters.bundles_submitted}, landed=0 — bundle/queue "
        f"self-conflict regression"
    )
    # Tips paid wired through to the searcher's tracker.
    assert counters.tips_paid_lamports > 0
    assert counters.realized_ev_lamports > 0


def test_sandwich_consumes_victim_from_regular_queue() -> None:
    """A sandwich bundle includes a verbatim copy of the victim. The engine
    must drop the original from the regular queue so it doesn't double-execute.
    Validates fix #1 (consumed_actions path).
    """
    from defi_sim.core.agent import Agent
    from defi_sim.core.types import AgentRole, AgentState, SwapAction

    captured_victim: list[SwapAction] = []

    class _VictimEmitter(Agent):
        def __init__(self) -> None:
            self.agent_id = "victim"
            self.state = AgentState(agent_id="victim", role=AgentRole("noise"))

        def decide(self, ctx):  # type: ignore[no-untyped-def]
            action = SwapAction(
                agent_id="victim",
                token_in="USDC",
                token_out="SOL",
                amount_in=10_000,
            )
            captured_victim.append(action)
            return [action]

    spec = copy.deepcopy(_VICTIM_SPEC)
    spec["num_rounds"] = 5
    engine = build_engine(spec)
    execution = engine._execution_model
    execution._submission_priors = SubmissionPathPriors(
        jito_relayer_landing_prob_baseline=1.0
    )
    execution._submission_rng = np.random.default_rng(seed=7)

    engine._agents = [a for a in engine._agents if a.agent_id != "victim"]
    victim_agent = _VictimEmitter()
    victim_agent.state.balances = {"USDC": 1_000_000_000_000, "SOL": 1_000_000_000_000}
    engine._agents.append(victim_agent)
    engine._agent_rngs["victim"] = np.random.default_rng(0)

    searcher = JitoSearcher(
        agent_id="searcher-sand",
        params=JitoSearcherParams(
            strategies=["sandwich"],
            tip_curve=TipCurveSpec(kind="linear"),
            min_ev_to_submit_lamports=1_000,
            tip_account="96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        ),
    )
    searcher.state.balances = {"SOL": 1_000_000_000_000, "USDC": 1_000_000_000_000}
    engine._agents.append(searcher)
    engine._agent_rngs[searcher.agent_id] = np.random.default_rng(0)

    # Capture the slot's pending list right after the searcher decides.
    seen_pending: list[list] = []
    orig_decide = searcher.decide

    def _trace_decide(ctx):  # type: ignore[no-untyped-def]
        seen_pending.append(list(ctx.pending_actions or []))
        return orig_decide(ctx)

    searcher.decide = _trace_decide  # type: ignore[assignment]

    engine.step()

    # Sanity: the searcher saw the victim swap in pending_actions.
    assert seen_pending and any(
        isinstance(a, SwapAction) for a in seen_pending[0]
    ), "searcher should have seen victim in pending_actions"
    # The bundle was submitted with the victim as a consumed action.
    assert searcher.metrics.by_strategy["sandwich"].bundles_submitted == 1
    # And the bundle landed: the auction did not drop on a self-conflict
    # (victim was removed from the regular queue before admit).
    counters = searcher.metrics.by_strategy["sandwich"]
    assert counters.bundles_landed == 1, (
        "sandwich bundle should have landed; consumed_actions filter "
        "should have removed the duplicated victim from slot_pending"
    )
