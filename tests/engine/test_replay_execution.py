"""Skeleton tests for :class:`ReplayExecution` (PRD US-002 line 280).

Pins the prescribed shape of the class — ctor accepting a slot stream,
counterfactual list, ``add_counterfactual``, ``pending_actions_for_agent``
returning ``[]``, and ``step_slot`` materializing the next snapshot and
running it through any registered counterfactuals. Substantive behavior
tests for the counterfactual / diff / artifact APIs land alongside their
own PRD bullets (lines 345-350).
"""

from __future__ import annotations

import inspect

import pytest

from defi_sim.core.types import Action
from defi_sim.engine.execution import BatchExecution
from defi_sim.engine.ordering import OrderingStrategy
from defi_sim.engine.replay_execution import (
    AgentInjectCounterfactual,
    Counterfactual,
    CounterfactualSpec,
    ErrorBand,
    FeeReplaceCounterfactual,
    OrderingReplaceCounterfactual,
    ReplayDiff,
    ReplayExecution,
    RunSnapshot,
    TipReplaceCounterfactual,
    extract_actual_metrics,
)
from defi_sim.engine.slot import ExecutedAction
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    MaterializedActionMetadata,
    MaterializedSwapAction,
)
from defi_sim_solana.replay.slot_client import SlotSnapshot


def _make_snapshot(slot: int) -> SlotSnapshot:
    return SlotSnapshot(slot=slot)


def test_replay_execution_subclasses_batch_execution() -> None:
    assert issubclass(ReplayExecution, BatchExecution)


def test_replay_execution_init_stores_slot_stream_and_empty_counterfactuals() -> None:
    stream = iter([_make_snapshot(100), _make_snapshot(101)])
    rx = ReplayExecution(slot_stream=stream)
    assert rx._slot_stream is stream
    assert rx._counterfactuals == []


def test_add_counterfactual_appends_in_order() -> None:
    class _Noop(Counterfactual):
        def apply(self, actions, slot, state):
            return actions

    rx = ReplayExecution(slot_stream=iter(()))
    a = _Noop()
    b = _Noop()
    rx.add_counterfactual(a)
    rx.add_counterfactual(b)
    assert rx._counterfactuals == [a, b]


def test_pending_actions_for_agent_returns_empty_list() -> None:
    rx = ReplayExecution(slot_stream=iter(()))
    # Replay mode ignores agent decisions: empty list, NOT None (which
    # would mean "fall through to default visibility").
    result = rx.pending_actions_for_agent(agent=None, pending=[], round=0)
    assert result == []


def test_replay_execution_consumes_slot_stream() -> None:
    snap0 = _make_snapshot(500)
    snap1 = _make_snapshot(501)
    rx = ReplayExecution(slot_stream=iter([snap0, snap1]))
    out0 = rx.step_slot(slot=500, state=None)
    out1 = rx.step_slot(slot=501, state=None)
    assert out0 is snap0
    assert out1 is snap1


def test_replay_with_no_counterfactuals_emits_historical_actions() -> None:
    # PRD line 346: with zero counterfactuals registered, the action list
    # passed downstream must be exactly what materialize_slot returns from
    # the historical SlotSnapshot — no synthetic actions inserted, no
    # mutations applied, ordering preserved.
    from defi_sim_solana.replay.materialize import materialize_slot

    snap = SlotSnapshot(
        slot=900,
        transactions=(
            {"signature": "tx0", "program_ids": ("11111111111111111111111111111111",)},
            {"signature": "tx1", "program_ids": ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",)},
            {"signature": "tx2", "program_ids": ("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",)},
        ),
    )
    expected = materialize_slot(snap)

    rx = ReplayExecution(slot_stream=iter([snap]))
    assert rx._counterfactuals == []
    out = rx.step_slot(slot=900, state=None)

    assert out is snap
    assert rx._last_replay_actions == expected
    assert len(rx._last_replay_actions) == len(snap.transactions)


def test_replay_with_tip_replace_counterfactual_modifies_one_bundle_only() -> None:
    # PRD line 347: TipReplaceCounterfactual must mutate the tip on actions
    # whose ``bundle_id`` matches its target, and leave every other action's
    # ``tip_lamports`` untouched (including actions in other bundles and
    # actions with no bundle).
    from dataclasses import dataclass

    @dataclass
    class _BundleTipAction(Action):
        bundle_id: str | None = None
        tip_lamports: int = 0

    a = _BundleTipAction(agent_id="a", bundle_id="bundle-A", tip_lamports=1_000)
    b = _BundleTipAction(agent_id="b", bundle_id="bundle-B", tip_lamports=2_000)
    c = _BundleTipAction(agent_id="c", bundle_id="bundle-A", tip_lamports=3_000)
    d = _BundleTipAction(agent_id="d", bundle_id=None, tip_lamports=4_000)
    actions: list[Action] = [a, b, c, d]

    cf = TipReplaceCounterfactual(target_bundle_id="bundle-A", new_tip_lamports=0)
    out = cf.apply(actions, slot=10, state=None)

    assert out is actions
    assert a.tip_lamports == 0
    assert c.tip_lamports == 0
    assert b.tip_lamports == 2_000
    assert d.tip_lamports == 4_000


def test_replay_with_agent_inject_counterfactual_adds_synthetic_actions() -> None:
    # PRD line 348: AgentInjectCounterfactual must call the wrapped agent's
    # decide() with a DecisionContext for the current slot and append the
    # returned actions to the historical stream — preserving historical
    # ordering and appending synthetics at the tail.
    from dataclasses import dataclass

    from defi_sim.core.agent import AgentState

    @dataclass
    class _Marker(Action):
        label: str = ""

    historical: list[Action] = [
        _Marker(agent_id="hist-0", label="h0"),
        _Marker(agent_id="hist-1", label="h1"),
    ]
    synthetic = [
        _Marker(agent_id="bot", label="s0"),
        _Marker(agent_id="bot", label="s1"),
    ]

    seen_ctxs: list[tuple[int, int]] = []

    class _StubAgent:
        agent_id = "bot"
        state = AgentState(agent_id="bot")

        def decide(self, ctx):
            seen_ctxs.append((ctx.current_round, ctx.current_slot))
            return list(synthetic)

    cf = AgentInjectCounterfactual(agent=_StubAgent())  # type: ignore[arg-type]
    out = cf.apply(historical, slot=777, state=None)

    assert out == historical + synthetic
    assert out[: len(historical)] == historical
    assert out[len(historical) :] == synthetic
    assert seen_ctxs == [(777, 777)]


def test_step_slot_runs_each_counterfactual_in_order() -> None:
    calls: list[tuple[str, int]] = []

    class _Tag(Counterfactual):
        def __init__(self, label: str) -> None:
            self.label = label

        def apply(self, actions: list[Action], slot: int, state):
            calls.append((self.label, slot))
            return actions

    rx = ReplayExecution(slot_stream=iter([_make_snapshot(7)]))
    rx.add_counterfactual(_Tag("a"))
    rx.add_counterfactual(_Tag("b"))
    rx.step_slot(slot=7, state=None)
    assert calls == [("a", 7), ("b", 7)]


def test_step_slot_runs_actions_through_admit_order_execute(
    monkeypatch,
) -> None:
    from dataclasses import dataclass

    import defi_sim_solana.replay.materialize as materialize_mod

    @dataclass
    class _Marker(Action):
        label: str = ""

    first = _Marker(agent_id="a", label="drop")
    second = _Marker(agent_id="b", label="execute")
    snap = _make_snapshot(11)
    monkeypatch.setattr(
        materialize_mod,
        "materialize_slot",
        lambda _snap: [first, second],
    )

    def admission_policy(actions, round, context):
        assert round == 11
        assert context.current_slot == 11
        return [actions[1]], [(actions[0], "replay_drop")]

    class _Reverse(OrderingStrategy):
        def order(self, actions, round, context=None):
            return list(reversed(actions))

    class _State:
        def __init__(self) -> None:
            self.executed: list[tuple[str, int]] = []

        def execute_replay_action(self, action, slot):
            self.executed.append((action.label, slot))
            return ExecutedAction(
                action=action,
                execution_cost=7,
                cost_token="COLLATERAL",
                succeeded=True,
            )

    state = _State()
    rx = ReplayExecution(
        slot_stream=iter([snap]),
        ordering=_Reverse(),
        admission_policy=admission_policy,
    )

    rx.step_slot(slot=11, state=state)

    assert rx._last_replay_submitted_actions == [first, second]
    assert rx._last_replay_actions == [second]
    assert rx._last_replay_dropped == [(first, "replay_drop")]
    assert state.executed == [("execute", 11)]
    assert rx._last_replay_outcome is not None
    assert rx._last_replay_outcome.executed[0].execution_cost == 7


def test_step_slot_fails_closed_without_replay_executor(
    monkeypatch,
) -> None:
    from dataclasses import dataclass

    import defi_sim_solana.replay.materialize as materialize_mod

    @dataclass
    class _Marker(Action):
        label: str = ""

    action = _Marker(agent_id="a", label="needs-executor")
    monkeypatch.setattr(materialize_mod, "materialize_slot", lambda _snap: [action])

    rx = ReplayExecution(slot_stream=iter([_make_snapshot(12)]))
    rx.step_slot(slot=12, state=None)

    assert rx._last_replay_outcome is not None
    executed = rx._last_replay_outcome.executed[0]
    assert executed.action is action
    assert executed.succeeded is False
    assert executed.failure_reason == "missing_replay_executor"


def test_counterfactual_is_abstract() -> None:
    assert inspect.isabstract(Counterfactual)
    with pytest.raises(TypeError):
        Counterfactual()  # type: ignore[abstract]


# ----- Diff API tests (PRD line 321) -----------------------------------------


def test_replay_diff_init_holds_predicted_and_actual() -> None:
    predicted = RunSnapshot(tips_paid=1000)
    actual = _make_snapshot(900)
    diff = ReplayDiff(predicted=predicted, actual=actual)
    assert diff.predicted is predicted
    assert diff.actual is actual


def test_per_metric_error_returns_dict_of_error_bands() -> None:
    predicted = RunSnapshot(tips_paid=500)
    actual = SlotSnapshot(slot=10, jito_tips=({"lamports": 500},))
    bands = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()
    assert isinstance(bands, dict)
    assert all(isinstance(b, ErrorBand) for b in bands.values())
    assert "tips_paid" in bands


def test_tips_paid_band_computes_zero_error_on_match() -> None:
    predicted = RunSnapshot(tips_paid=750)
    actual = SlotSnapshot(slot=10, jito_tips=({"lamports": 750},))
    band = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()["tips_paid"]
    assert band.supported is True
    assert band.predicted == 750.0
    assert band.actual == 750.0
    assert band.abs_error == 0.0
    assert band.rel_error == 0.0


def test_tips_paid_band_computes_nonzero_error() -> None:
    predicted = RunSnapshot(tips_paid=1200)
    actual = SlotSnapshot(slot=10, jito_tips=({"lamports": 1000},))
    band = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()["tips_paid"]
    assert band.supported is True
    assert band.abs_error == 200.0
    assert band.rel_error == pytest.approx(0.2)


def test_pool_price_bands_marked_unsupported_until_decoders_land() -> None:
    predicted = RunSnapshot(pool_prices={"sol_usdc": 100.5})
    actual = _make_snapshot(10)
    bands = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()
    band = bands["pool_price:sol_usdc"]
    assert band.supported is False
    assert band.actual is None
    assert band.abs_error is None
    assert band.rel_error is None
    assert band.predicted == 100.5


def test_lp_balance_bands_marked_unsupported_until_decoders_land() -> None:
    predicted = RunSnapshot(lp_balances={"agent_a": 42.0, "agent_b": 7.5})
    actual = _make_snapshot(10)
    bands = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()
    assert bands["lp_balance:agent_a"].supported is False
    assert bands["lp_balance:agent_b"].supported is False
    assert bands["lp_balance:agent_a"].predicted == 42.0


def test_total_volume_and_liquidations_bands_marked_unsupported() -> None:
    predicted = RunSnapshot(total_volume=1234.0, liquidations_triggered=3)
    actual = _make_snapshot(10)
    bands = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()
    assert bands["total_volume"].supported is False
    assert bands["liquidations_triggered"].supported is False
    assert bands["total_volume"].predicted == 1234.0
    assert bands["liquidations_triggered"].predicted == 3.0


def test_decoded_swap_actuals_support_volume_and_pool_price_bands(
    monkeypatch,
) -> None:
    import defi_sim_solana.replay.materialize as materialize_mod

    action = MaterializedSwapAction(
        agent_id="trader",
        token_in="SOL",
        token_out="USDC",
        amount_in=100,
        amount_out=95,
        pool_id="pool-1",
        materialized_metadata=MaterializedActionMetadata(
            decode_status=ActionDecodeStatus.DECODED,
            slot=10,
            program_ids=("whirlpool",),
        ),
    )
    monkeypatch.setattr(materialize_mod, "materialize_slot", lambda _snap: [action])

    predicted = RunSnapshot(total_volume=120.0, pool_prices={"pool-1": 1.0})
    actual = _make_snapshot(10)
    bands = ReplayDiff(predicted=predicted, actual=actual).per_metric_error()

    assert bands["total_volume"].supported is True
    assert bands["total_volume"].actual == 100.0
    assert bands["total_volume"].abs_error == 20.0
    assert bands["pool_price:pool-1"].supported is True
    assert bands["pool_price:pool-1"].actual == 0.95
    assert bands["pool_price:pool-1"].abs_error == pytest.approx(0.05)


def test_extract_actual_metrics_sums_jito_tips() -> None:
    actual = SlotSnapshot(
        slot=10,
        jito_tips=(
            {"lamports": 100},
            {"lamports": 250},
            {"amount": 50},
        ),
    )
    metrics = extract_actual_metrics(actual)
    assert metrics.tips_paid == 400


def test_extract_actual_metrics_counts_unsupported_instructions() -> None:
    actual = SlotSnapshot(slot=10, transactions=({"x": 1}, {"x": 2}, {"x": 3}))
    metrics = extract_actual_metrics(actual)
    # All transactions are "unsupported" until decoders register; Phase 2.3+
    # will reduce this count as per-protocol hydrators land.
    assert metrics.unsupported_instruction_coverage == 3


def test_replay_reports_unsupported_instruction_coverage() -> None:
    actual = SlotSnapshot(slot=10, transactions=({"x": 1}, {"x": 2}))
    diff = ReplayDiff(predicted=RunSnapshot(), actual=actual)
    assert diff.unsupported_instruction_coverage == 2


def test_run_snapshot_to_dict_includes_all_seven_replay_metrics() -> None:
    # PRD US-006 line 989: a replay run snapshot must include all seven
    # replay metric calculators' results under ``metrics.replay``. Pin the
    # full key set so a missing or renamed calculator fails loudly.
    from defi_sim.core.types import BundleOutcome

    snap = RunSnapshot(
        bundle_outcomes=[
            BundleOutcome(
                slot=1,
                bundle_index=0,
                status="landed",
                tip_lamports=1_000,
                validator_revenue_lamports=0,
                stake_pool_revenue_lamports=0,
            ),
            BundleOutcome(
                slot=1,
                bundle_index=1,
                status="dropped",
                tip_lamports=0,
                validator_revenue_lamports=0,
                stake_pool_revenue_lamports=0,
            ),
        ],
        tip_efficiency_samples=[(1_000, 5_000)],
        slot_inclusion_samples=[(100, 102)],
        breakeven_samples=[(1_000, 5_000), (3_000, 6_000)],
        skip_rate_samples=[(False, 100), (True, 50)],
        write_lock_claims=[("orca_pool", 100), ("orca_pool", 100)],
        submission_path_samples=[("jito_relay", True), ("public_rpc", False)],
    )

    out = snap.to_dict()

    assert "metrics" in out
    assert "replay" in out["metrics"]
    replay = out["metrics"]["replay"]
    assert set(replay.keys()) == {
        "bundle_landing_rate",
        "tip_efficiency",
        "slot_inclusion_latency",
        "cu_per_dollar_tip_breakeven",
        "skip_rate_cost",
        "write_lock_heatmap",
        "submission_path_comparison",
    }
    # Spot-check a few computed values to pin the wiring (not just the keys).
    assert replay["bundle_landing_rate"]["value"] == 0.5
    assert replay["bundle_landing_rate"]["sample_size"] == 2
    assert replay["tip_efficiency"]["value"] == 0.2
    assert replay["skip_rate_cost"]["value"] == 50.0
    assert replay["write_lock_heatmap"]["max_contention"] == 2


def test_run_snapshot_to_dict_with_empty_inputs_still_surfaces_seven_metrics() -> None:
    # Default snapshot has no replay inputs — each calculator returns its
    # zero-sample sentinel. The seven keys must still be present so chart
    # callers can render "no data" instead of "missing field".
    out = RunSnapshot().to_dict()
    replay = out["metrics"]["replay"]
    assert len(replay) == 7
    for entry in replay.values():
        assert entry["sample_size"] == 0


# ----- Counterfactual spec serialization (PRD line 331) -----------------------


def test_counterfactual_spec_to_dict_round_trip() -> None:
    spec = CounterfactualSpec(kind="X", params={"a": 1, "b": "y"})
    assert spec.to_dict() == {"kind": "X", "params": {"a": 1, "b": "y"}}


def test_tip_replace_counterfactual_to_spec() -> None:
    cf = TipReplaceCounterfactual(target_bundle_id="bundle-7", new_tip_lamports=12345)
    spec = cf.to_spec()
    assert spec.kind == "TipReplaceCounterfactual"
    assert spec.params == {"target_bundle_id": "bundle-7", "new_tip_lamports": 12345}


def test_fee_replace_counterfactual_to_spec() -> None:
    cf = FeeReplaceCounterfactual(target_pool="sol_usdc", new_fee_bps=30)
    spec = cf.to_spec()
    assert spec.kind == "FeeReplaceCounterfactual"
    assert spec.params == {"target_pool": "sol_usdc", "new_fee_bps": 30}


def test_ordering_replace_counterfactual_to_spec_uses_repr_for_scheduler() -> None:
    class _Sched:
        def __repr__(self) -> str:
            return "<_Sched>"

    cf = OrderingReplaceCounterfactual(new_scheduler=_Sched())  # type: ignore[arg-type]
    spec = cf.to_spec()
    assert spec.kind == "OrderingReplaceCounterfactual"
    assert spec.params == {"new_scheduler": "<_Sched>"}


def test_agent_inject_counterfactual_to_spec_uses_repr_for_agent() -> None:
    class _Agent:
        def __repr__(self) -> str:
            return "<_Agent id=42>"

        def decide(self, ctx):  # noqa: D401 - test stub
            return []

    cf = AgentInjectCounterfactual(agent=_Agent())  # type: ignore[arg-type]
    spec = cf.to_spec()
    assert spec.kind == "AgentInjectCounterfactual"
    assert spec.params == {"agent": "<_Agent id=42>"}


# ----- Replay run artifact persistence (PRD line 331) ------------------------


def test_replay_run_artifact_kind_is_replay(tmp_path, monkeypatch) -> None:
    from defi_sim_api.backend.store import (
        ARTIFACT_ROOT_ENV,
        get_artifact_store,
        reset_artifact_store,
    )
    from defi_sim_api.backend.runtime import persist_replay_run

    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    reset_artifact_store()
    try:
        cfs = [
            TipReplaceCounterfactual(target_bundle_id="b1", new_tip_lamports=0),
            FeeReplaceCounterfactual(target_pool="sol_usdc", new_fee_bps=15),
        ]
        record = persist_replay_run(
            "run-replay-1",
            slot_range=(100, 102),
            counterfactuals=cfs,
            predicted={"tips_paid": 1234},
            replay_diff={"tips_paid": {"abs_error": 0}},
        )
        assert record["run_id"] == "run-replay-1"
        assert record["source"] == "replay"
        assert record["summary"]["kind"] == "replay"
        cf_summary = record["summary"]["counterfactuals"]
        assert isinstance(cf_summary, list) and len(cf_summary) == 2
        assert cf_summary[0]["kind"] == "TipReplaceCounterfactual"
        assert cf_summary[0]["params"]["target_bundle_id"] == "b1"
        assert cf_summary[1]["kind"] == "FeeReplaceCounterfactual"
        assert record["summary"]["slot_range"] == [100, 102]

        store = get_artifact_store()
        spec = store.get_run_spec("run-replay-1")
        assert spec is not None
        assert spec["kind"] == "replay"
        assert spec["counterfactuals"][0]["kind"] == "TipReplaceCounterfactual"

        result = store.get_run_result("run-replay-1")
        assert result is not None
        assert result["kind"] == "replay"
        assert result["replay_diff"] == {"tips_paid": {"abs_error": 0}}
    finally:
        reset_artifact_store()


def test_persist_replay_run_with_no_counterfactuals(tmp_path, monkeypatch) -> None:
    from defi_sim_api.backend.store import ARTIFACT_ROOT_ENV, reset_artifact_store
    from defi_sim_api.backend.runtime import persist_replay_run

    monkeypatch.setenv(ARTIFACT_ROOT_ENV, str(tmp_path))
    reset_artifact_store()
    try:
        record = persist_replay_run("run-replay-2", slot_range=(7, 7))
        assert record["summary"]["kind"] == "replay"
        assert record["summary"]["counterfactuals"] == []
        assert record["summary"]["slot_range"] == [7, 7]
    finally:
        reset_artifact_store()
