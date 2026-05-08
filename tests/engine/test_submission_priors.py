"""PRD US-004 line 377: ``test_rpc_path_drops_at_configured_rate``.

Verifies the submission-path Bernoulli sampler in
``SolanaLikeExecution.admit()`` drops RPC-submitted actions at the
configured ``rpc_landing_prob_baseline``. With ``landing_prob=0.5`` over
10_000 actions, the observed drop count must be within ~2σ of the
expected mean (5000 ± 100).

Determinism is pinned by passing an explicitly seeded
``numpy.random.Generator`` to the execution model so the test cannot
become flaky on CI.
"""

from __future__ import annotations

import numpy as np

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.market import Market
from defi_sim.core.types import (
    Action,
    AgentState,
    BundleAction,
    ExecutionContext,
    ExecutionResult,
    MarketSnapshot,
    Side,
    SwapAction,
)
from defi_sim.engine.config import SimulationConfig
from defi_sim.engine.execution import DropReason, SolanaLikeExecution
from defi_sim.engine.simulation import SimulationEngine
from defi_sim.engine.submission_priors import SubmissionPathPriors


def test_rpc_path_drops_at_configured_rate() -> None:
    priors = SubmissionPathPriors(rpc_landing_prob_baseline=0.5)
    rng = np.random.default_rng(seed=42)
    exec_model = SolanaLikeExecution(submission_priors=priors, submission_rng=rng)

    n = 10_000
    actions = [
        SwapAction(
            agent_id=f"agent-{i}",
            token_in="SOL",
            token_out="USDC",
            amount_in=1,
        )
        for i in range(n)
    ]

    admitted, dropped = exec_model.admit(actions, round=0)

    drop_count = sum(
        1 for _, reason in dropped if reason == DropReason.SUBMISSION_PATH_DROP
    )

    expected_mean = n * 0.5
    sigma = (n * 0.5 * 0.5) ** 0.5
    assert abs(drop_count - expected_mean) <= 2 * sigma, (
        f"observed {drop_count} drops; expected {expected_mean} ± {2 * sigma:.0f} (2σ)"
    )

    # Sanity: every non-dropped action must be admitted (no other drop reason
    # should fire for a default SwapAction on the rpc path with default CU).
    other_drops = [
        (a, r) for a, r in dropped if r != DropReason.SUBMISSION_PATH_DROP
    ]
    assert other_drops == [], f"unexpected non-submission drops: {other_drops}"
    assert len(admitted) + drop_count == n


def test_tpu_quic_higher_default_than_rpc() -> None:
    """PRD US-004 line 378: TPU/QUIC default landing prob exceeds RPC default.

    A bare ``SubmissionPathPriors()`` must encode the prior that direct
    TPU/QUIC submission to the leader lands more reliably than going
    through a public RPC relay.
    """
    priors = SubmissionPathPriors()
    assert priors.tpu_quic_landing_prob_baseline > priors.rpc_landing_prob_baseline


def test_jito_relayer_path_drop_only_for_tipped_bundles() -> None:
    """PRD US-004 line 379: jito_relayer admits no bare Actions.

    Jito bundles flow through ``SolanaLikeExecution.submit_bundle`` —
    out-of-band from ``admit`` — so any individual ``Action`` arriving at
    ``admit`` with ``submission_path == "jito_relayer"`` is structurally
    invalid and must be rejected with ``INVALID_SUBMISSION_PATH``. The
    pre-existing ``BundleAction`` core type is a multi-asset weighted
    basket trade and is *not* a Jito bundle, so it must also be rejected
    on this path (US-013 wiring fix removed the misleading
    ``isinstance(action, BundleAction)`` exemption).
    """
    priors = SubmissionPathPriors(jito_relayer_landing_prob_baseline=1.0)
    rng = np.random.default_rng(seed=0)
    exec_model = SolanaLikeExecution(submission_priors=priors, submission_rng=rng)

    swap = SwapAction(
        agent_id="agent-swap",
        token_in="SOL",
        token_out="USDC",
        amount_in=1,
        submission_path="jito_relayer",
    )
    basket = BundleAction(
        agent_id="agent-basket",
        collateral="USDC",
        amount=10,
        weights={"SOL": 1.0},
        side=Side.BUY,
        submission_path="jito_relayer",
    )

    _, dropped = exec_model.admit([swap, basket], round=0)

    drop_map = {a.agent_id: r for a, r in dropped}
    assert drop_map.get("agent-swap") == DropReason.INVALID_SUBMISSION_PATH
    assert drop_map.get("agent-basket") == DropReason.INVALID_SUBMISSION_PATH


class _NoopMarket(Market):
    market_type = "noop"

    def get_state(self) -> MarketSnapshot:
        return MarketSnapshot(tokens=["SOL"])

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        return ExecutionResult(success=True)

    def copy(self) -> "_NoopMarket":
        return _NoopMarket()

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "_NoopMarket":
        return cls()


class _IdleAgent(Agent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.state = AgentState(agent_id=agent_id)

    def decide(self, ctx: DecisionContext) -> list[Action]:
        return []


def test_priors_calibrated_at_serialized_to_snapshot() -> None:
    """PRD US-004 line 380: ``calibrated_at`` round-trips through run metadata.

    The run snapshot's ``metadata["submission_priors"]["calibrated_at"]``
    must reflect whatever value was set on the engine's
    ``SubmissionPathPriors``. ``metadata["priors_calibrated_at"]`` is the
    consumer-facing marker: it surfaces the literal calibration timestamp
    when present, and the sentinel string ``"synthetic"`` when not.

    FIX-020: defaults are now calibrated (``calibrated_at`` defaults to
    the FIX-020 capture date). The synthetic case is reached by explicitly
    passing ``calibrated_at=None``.
    """
    # Synthetic case: calibrated_at is explicitly None.
    synthetic_priors = SubmissionPathPriors(calibrated_at=None)
    synthetic_engine = SimulationEngine(
        _NoopMarket(),
        [_IdleAgent("idle")],
        SimulationConfig(
            num_rounds=1,
            execution_model=SolanaLikeExecution(submission_priors=synthetic_priors),
        ),
    )
    synthetic_result = synthetic_engine.run()
    assert synthetic_result.metadata["submission_priors"]["calibrated_at"] is None
    assert synthetic_result.metadata["priors_calibrated_at"] == "synthetic"

    # Calibrated case: calibrated_at is an ISO timestamp.
    timestamp = "2026-01-15T00:00:00Z"
    calibrated_priors = SubmissionPathPriors(calibrated_at=timestamp)
    calibrated_engine = SimulationEngine(
        _NoopMarket(),
        [_IdleAgent("idle")],
        SimulationConfig(
            num_rounds=1,
            execution_model=SolanaLikeExecution(submission_priors=calibrated_priors),
        ),
    )
    calibrated_result = calibrated_engine.run()
    assert calibrated_result.metadata["submission_priors"]["calibrated_at"] == timestamp
    assert calibrated_result.metadata["priors_calibrated_at"] == timestamp


def test_priors_default_includes_fix020_calibration() -> None:
    """FIX-020: ``SubmissionPathPriors()`` defaults now carry a calibrated_at."""
    priors = SubmissionPathPriors()
    assert priors.calibrated_at is not None, (
        "FIX-020 calibrated the default jito_relayer prior; "
        "calibrated_at must reflect the capture date"
    )
    # The Jito-relayer prior must be in (0, 1] — calibrated, not floor-bypass.
    assert 0.0 < priors.jito_relayer_landing_prob_baseline <= 1.0
    # RPC + TPU/QUIC remain explicitly illustrative (scope-cut in docstring).
    assert priors.rpc_landing_prob_baseline == 0.85
    assert priors.tpu_quic_landing_prob_baseline == 0.95
