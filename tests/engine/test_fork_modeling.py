"""Fork-modeling tests (PRD US-014 lines 1140-1145).

The engine rolls a per-slot Bernoulli at ``fork_probability_per_slot``
and emits a ``ForkReorgEvent`` on a hit (PRD line 1117). This file
covers the boundary cases: fork_probability=0 must never emit an event;
fork_probability=1 must emit on every slot.
"""

from __future__ import annotations

from defi_sim.core.types import ForkReorgEvent, SwapAction
from defi_sim.engine.bundle import Bundle, TipPayment
from defi_sim.engine.bundle_auction import BundleAuction
from defi_sim.engine.events import Event, EventType
from defi_sim.engine.execution import SolanaLikeExecution
from defi_sim.engine.fork import ChainReorgForkSpec
from defi_sim.engine.ordering import OrderingContext
from defi_sim.engine.slot import BundleExecutionResult, ExecutedAction, SlotContext
from defi_sim.engine.transactions import VersionedTransaction


def _executor(action, slot_index):
    return ExecutedAction(
        action=action,
        execution_cost=0,
        cost_token=None,
        succeeded=True,
    )


def _drive_slots(model: SolanaLikeExecution, num_slots: int) -> list[Event]:
    captured: list[Event] = []
    for slot in range(num_slots):
        ctx = SlotContext(
            slot=slot,
            pending_actions=[],
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: captured.append(event),
        )
        model.execute_slot(ctx)
    return captured


def test_fork_probability_zero_no_reorg() -> None:
    """With ``fork_probability_per_slot=0``, no fork events are emitted
    over a long run (PRD line 1140).
    """
    spec = ChainReorgForkSpec(fork_probability_per_slot=0.0, max_reorg_depth_slots=5, seed=42)
    model = SolanaLikeExecution(fork_spec=spec)

    captured = _drive_slots(model, num_slots=1000)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert fork_events == []


def test_fork_probability_one_always_reorg() -> None:
    """With ``fork_probability_per_slot=1.0``, every slot emits a
    ``ForkReorgEvent`` (PRD line 1141).
    """
    spec = ChainReorgForkSpec(fork_probability_per_slot=1.0, max_reorg_depth_slots=3, seed=42)
    model = SolanaLikeExecution(fork_spec=spec)

    num_slots = 50
    captured = _drive_slots(model, num_slots=num_slots)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert len(fork_events) == num_slots
    for slot, event in enumerate(fork_events):
        payload = event.data["fork_reorg"]
        assert isinstance(payload, ForkReorgEvent)
        assert payload.fork_point_slot == slot
        assert event.round == slot
        assert 1 <= payload.depth <= 3


def test_reorg_depth_bounded_by_max_reorg_depth_slots() -> None:
    """Sampled reorg depths never exceed ``max_reorg_depth_slots`` and are
    always at least 1 slot deep (PRD line 1142). Drives many slots with
    fork_probability=1.0 so every slot fires the depth roll, then asserts
    the bound on the full distribution.
    """
    max_depth = 7
    spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=max_depth,
        seed=12345,
    )
    model = SolanaLikeExecution(fork_spec=spec)

    num_slots = 500
    captured = _drive_slots(model, num_slots=num_slots)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert len(fork_events) == num_slots

    depths = [event.data["fork_reorg"].depth for event in fork_events]
    assert all(1 <= d <= max_depth for d in depths)
    assert min(depths) == 1
    assert max(depths) == max_depth


def test_fork_reorg_event_payload_populated_from_history() -> None:
    """``ForkReorgEvent.abandoned_actions_count`` is populated from the
    rolling per-slot history buffer covering the inclusive abandoned
    range ``[fork_point_slot - depth, fork_point_slot]`` (PRD line 1117
    + line 1123). Drives several slots with admitted actions per slot,
    then verifies each slot's fork event reports the expected count
    based on the actual depth that was rolled.
    """
    max_depth = 3
    actions_per_slot = 2
    spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=max_depth,
        seed=42,
    )
    model = SolanaLikeExecution(fork_spec=spec)
    captured: list[Event] = []

    num_slots = 6
    for slot in range(num_slots):
        actions = [
            SwapAction(
                agent_id=f"trader-{slot}-{i}",
                token_in="SOL",
                token_out="USDC",
                amount_in=1,
            )
            for i in range(actions_per_slot)
        ]
        ctx = SlotContext(
            slot=slot,
            pending_actions=actions,
            ordering_context=OrderingContext(),
            executor=_executor,
            emit=lambda event: captured.append(event),
        )
        model.execute_slot(ctx)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert len(fork_events) == num_slots

    for slot, event in enumerate(fork_events):
        payload = event.data["fork_reorg"]
        depth = payload.depth
        # Buffer holds at most max_depth + 1 slots, so the inclusive
        # abandoned range [slot - depth, slot] is fully covered for the
        # slot indices driven here. Negative slot indices clamp to 0.
        min_slot = max(0, slot - depth)
        expected_slots_in_range = slot - min_slot + 1
        assert (
            payload.abandoned_actions_count
            == expected_slots_in_range * actions_per_slot
        ), f"slot={slot}, depth={depth}, count={payload.abandoned_actions_count}"
        # No bundles submitted in this test → tuple is empty.
        assert payload.abandoned_bundle_ids == ()


def test_bundle_in_abandoned_slot_does_not_pay_tip() -> None:
    """A bundle that landed in a slot which the fork roll abandons does NOT
    pay its tip (PRD line 1124 / line 1143). The fork roll fires after
    bundle execution at end-of-slot; bundles in ``_last_slot_selected_bundles``
    are marked reverted with empty ``paid_tips`` so downstream revenue
    crediting skips them.
    """
    auction = BundleAuction(max_bundles_per_slot=5)
    spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=1,
        seed=42,
    )
    model = SolanaLikeExecution(bundle_auction=auction, fork_spec=spec)

    bundle = Bundle(
        txs=[
            VersionedTransaction(
                actions=[
                    SwapAction(
                        agent_id="searcher",
                        token_in="SOL",
                        token_out="USDC",
                        amount_in=1,
                    )
                ]
            )
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=10_000,
                recipient="tip-1",
            )
        ],
    )
    model.submit_bundle(bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(
                    action=tx.actions[0],
                    execution_cost=0,
                    cost_token=None,
                    succeeded=True,
                )
                for tx in b.txs
            ],
            paid_tips=list(b.paid_tip_payments(reverted=False, failed_at_index=None)),
        )

    captured: list[Event] = []
    ctx = SlotContext(
        slot=0,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: captured.append(event),
        execute_bundle=exec_bundle,
    )
    model.execute_slot(ctx)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert len(fork_events) == 1

    assert len(model._last_slot_selected_bundles) == 1
    _selected_bundle, result = model._last_slot_selected_bundles[0]
    assert result.reverted is True
    assert result.paid_tips == []


def test_validator_revenue_reverted_after_fork() -> None:
    """A bundle that lands in an abandoned slot does NOT credit the
    leader-validator's MEV revenue (PRD line 1144 / line 1131). With
    ``fork_probability_per_slot=1.0``, the fork roll fires on the same
    slot the bundle lands in; the bundle is marked reverted before
    ``SimulationEngine._credit_validator_revenue`` runs, so the
    validator's SOL balance and the per-epoch revenue accumulator both
    stay empty.
    """
    import copy

    from defi_sim.agents.validator import Validator, ValidatorParams
    from defi_sim.engine.api import build_engine
    from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS
    from defi_sim.engine.leader_schedule import LeaderSchedule

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
                "agent_id": "searcher",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
            },
        ],
        "num_rounds": 1,
        "seed": 11,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
        },
    }
    engine = build_engine(copy.deepcopy(spec))

    auction = BundleAuction()
    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=1,
        seed=42,
    )
    execution = SolanaLikeExecution(bundle_auction=auction, fork_spec=fork_spec)
    engine._execution_model = execution

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.05,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)

    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    pre_balance = validator.state.balances.get("SOL", 0)

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS
    bundle = Bundle(
        txs=[
            VersionedTransaction(
                actions=[
                    SwapAction(
                        agent_id="searcher",
                        token_in="SOL",
                        token_out="USDC",
                        amount_in=1,
                    )
                ]
            )
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=tip,
                recipient="tip-acct",
            )
        ],
    )
    execution.submit_bundle(bundle)

    def exec_bundle(b: Bundle, slot: int) -> BundleExecutionResult:
        return BundleExecutionResult(
            reverted=False,
            failed_at_index=None,
            failed_reason=None,
            executed=[
                ExecutedAction(
                    action=tx.actions[0],
                    execution_cost=0,
                    cost_token=None,
                    succeeded=True,
                )
                for tx in b.txs
            ],
            paid_tips=list(b.paid_tip_payments(reverted=False, failed_at_index=None)),
        )

    captured: list[Event] = []
    ctx = SlotContext(
        slot=1,
        pending_actions=[],
        ordering_context=OrderingContext(),
        executor=_executor,
        emit=lambda event: captured.append(event),
        execute_bundle=exec_bundle,
    )
    execution.execute_slot(ctx)
    engine._credit_validator_revenue(slot=1)

    fork_events = [e for e in captured if e.type == EventType.FORK_REORG]
    assert len(fork_events) == 1

    assert validator.state.balances.get("SOL", 0) == pre_balance
    assert engine.validator_revenue_by_epoch == {}


def test_actions_in_abandoned_slot_do_not_persist_state_changes() -> None:
    """A SwapAction admitted to an abandoned slot does NOT persist its
    state mutations (PRD line 1145 / line 1119). With
    ``fork_probability_per_slot=1.0``, the fork roll fires at the end of
    the same slot the swap lands in; ``SimulationEngine`` then restores
    the pre-slot snapshot of bundle-mutable state (agents, market(s),
    fees, RNGs), so market reserves and the swapper's balances revert to
    their pre-slot values.
    """
    import copy as _copy

    from defi_sim.core.agent import Agent, DecisionContext
    from defi_sim.core.types import Action, AgentRole, AgentState
    from defi_sim.engine.api import build_engine

    class _ScriptedAgent(Agent):
        def __init__(self, agent_id: str, actions: list[Action]) -> None:
            self.agent_id = agent_id
            self.state = AgentState(agent_id=agent_id, role=AgentRole("trader"))
            self._actions = list(actions)

        def decide(self, ctx: DecisionContext) -> list[Action]:
            return list(self._actions)

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
                "agent_id": "noise-stub",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
            },
        ],
        "num_rounds": 1,
        "seed": 11,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
        },
    }
    engine = build_engine(_copy.deepcopy(spec))

    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=1,
        seed=42,
    )
    engine._execution_model = SolanaLikeExecution(fork_spec=fork_spec)

    swapper = _ScriptedAgent(
        "swapper",
        [SwapAction(
            agent_id="swapper",
            token_in="SOL",
            token_out="USDC",
            amount_in=1_000,
        )],
    )
    swapper.state.balances["SOL"] = 1_000_000
    swapper.state.balances["USDC"] = 0
    engine._agents.append(swapper)
    import numpy as np
    engine._agent_rngs[swapper.agent_id] = np.random.default_rng(0)

    pre_reserves = dict(engine._market._reserves)
    pre_swapper_sol = swapper.state.balances.get("SOL", 0)
    pre_swapper_usdc = swapper.state.balances.get("USDC", 0)

    fork_events: list[Event] = []
    engine._bus.on(
        EventType.FORK_REORG,
        lambda event: fork_events.append(event),
    )

    engine.step()

    assert len(fork_events) == 1
    assert engine._market._reserves == pre_reserves
    assert swapper.state.balances.get("SOL", 0) == pre_swapper_sol
    assert swapper.state.balances.get("USDC", 0) == pre_swapper_usdc


def test_bundle_state_in_abandoned_slot_does_not_persist_state_changes() -> None:
    """A selected Jito bundle that is abandoned by a same-slot fork reports as
    reverted and also rolls back the market/agent mutations from its inner
    actions.
    """
    import copy

    from defi_sim.engine.api import build_engine
    from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS

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
                "agent_id": "searcher",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
            },
        ],
        "num_rounds": 1,
        "seed": 11,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
        },
    }
    engine = build_engine(copy.deepcopy(spec))
    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=1.0,
        max_reorg_depth_slots=1,
        seed=42,
    )
    execution = SolanaLikeExecution(
        bundle_auction=BundleAuction(),
        fork_spec=fork_spec,
    )
    engine._execution_model = execution

    bundle = Bundle(
        txs=[
            VersionedTransaction(
                actions=[
                    SwapAction(
                        agent_id="searcher",
                        token_in="SOL",
                        token_out="USDC",
                        amount_in=1_000,
                    )
                ]
            )
        ],
        tip_payments=[
            TipPayment(
                tx_index=0,
                location="standalone_tx",
                lamports=MIN_BUNDLE_TIP_LAMPORTS,
                recipient="tip-acct",
            )
        ],
    )
    execution.submit_bundle(bundle)
    searcher = engine._find_agent("searcher")
    assert searcher is not None
    pre_reserves = dict(engine._market._reserves)
    pre_balances = dict(searcher.state.balances)

    engine.step()

    assert len(execution._last_slot_selected_bundles) == 1
    _selected, result = execution._last_slot_selected_bundles[0]
    assert result.reverted is True
    assert result.paid_tips == []
    assert engine._market._reserves == pre_reserves
    assert searcher.state.balances == pre_balances


def test_past_slot_bundle_credits_reverted_on_fork() -> None:
    """A bundle that landed in a PAST slot in the abandoned range has
    its already-credited validator revenue rolled back when the fork
    fires (PRD line 1124 / line 1130).

    Pattern: build an engine with ``ChainReorgForkSpec(prob=0.0)`` so prior slots
    accrue validator credits normally, then flip ``fork_probability``
    to 1.0 just before the fork-trigger slot. The engine's pre-slot
    snapshot for slot ``N - d`` (which captured the accumulator BEFORE
    credits from the abandoned range applied) gets restored on the
    fork hit, debiting credits from slots ``[N - d, N - 1]`` AND the
    validator's SOL balance in one go. Slot ``N`` itself credits zero
    because the current-slot revert (iter 64) marks its bundle reverted.
    """
    import copy

    from defi_sim.agents.validator import Validator, ValidatorParams
    from defi_sim.engine.api import build_engine
    from defi_sim.engine.bundle import MIN_BUNDLE_TIP_LAMPORTS
    from defi_sim.engine.leader_schedule import LeaderSchedule

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
                "agent_id": "searcher",
                "params": {"collateral": "USDC", "frequency": 0.0},
                "initial_balances": {"USDC": 1_000_000_000, "SOL": 1_000_000_000},
            },
        ],
        "num_rounds": 10,
        "seed": 11,
        "execution": {
            "type": "solana_like",
            "ordering": {"type": "priority"},
            "gas_model": {"type": "compute_unit"},
        },
    }
    engine = build_engine(copy.deepcopy(spec))

    auction = BundleAuction()
    fork_spec = ChainReorgForkSpec(
        fork_probability_per_slot=0.0,
        max_reorg_depth_slots=3,
        seed=42,
    )
    execution = SolanaLikeExecution(bundle_auction=auction, fork_spec=fork_spec)
    engine._execution_model = execution

    validator = Validator(
        "validator-1",
        ValidatorParams(
            pubkey="val-pk-1",
            client="jito_solana",
            stake_pool_share=0.0,
            stake_lamports=1_000_000_000,
        ),
    )
    engine._agents.append(validator)
    import numpy as np
    engine._agent_rngs[validator.agent_id] = np.random.default_rng(0)
    execution._leader_schedule = LeaderSchedule.from_validator_agents([validator])

    tip = 100_000 * MIN_BUNDLE_TIP_LAMPORTS

    def make_bundle() -> Bundle:
        return Bundle(
            txs=[
                VersionedTransaction(
                    actions=[
                        SwapAction(
                            agent_id="searcher",
                            token_in="SOL",
                            token_out="USDC",
                            amount_in=1,
                        )
                    ]
                )
            ],
            tip_payments=[
                TipPayment(
                    tx_index=0,
                    location="standalone_tx",
                    lamports=tip,
                    recipient="tip-acct",
                )
            ],
        )

    pre_balance = validator.state.balances.get("SOL", 0)

    fork_events: list[Event] = []
    engine._bus.on(EventType.FORK_REORG, lambda event: fork_events.append(event))

    pre_fork_slots = 3
    for _slot in range(pre_fork_slots):
        execution.submit_bundle(make_bundle())
        engine.step()

    assert len(fork_events) == 0
    epoch_revenue = engine.validator_revenue_by_epoch
    accumulated_pre_fork = sum(
        entry.validator_revenue_lamports
        for bucket in epoch_revenue.values()
        for entry in bucket.values()
    )
    assert accumulated_pre_fork == tip * pre_fork_slots
    assert validator.state.balances.get("SOL", 0) - pre_balance == tip * pre_fork_slots

    fork_spec.fork_probability_per_slot = 1.0
    execution.submit_bundle(make_bundle())
    engine.step()

    assert len(fork_events) == 1
    depth = int(fork_events[0].data["depth"])
    assert 1 <= depth <= 3

    abandoned_credits = min(depth, pre_fork_slots) * tip
    expected_remaining = pre_fork_slots * tip - abandoned_credits

    epoch_revenue_post = engine.validator_revenue_by_epoch
    accumulated_post_fork = sum(
        entry.validator_revenue_lamports
        for bucket in epoch_revenue_post.values()
        for entry in bucket.values()
    )
    assert accumulated_post_fork == expected_remaining
    assert validator.state.balances.get("SOL", 0) - pre_balance == expected_remaining
