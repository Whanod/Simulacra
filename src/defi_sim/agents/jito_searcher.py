"""Jito searcher agent — synthetic-mode in Phase 1.

PRD US-013 (line 999): A `JitoSearcher` monitors the mempool / bundle pool for
opportunities and submits Jito bundles tipping the leader. Strategies are
slot-based: `backrun` and `sandwich` ship as first-class strategies in 1.11;
`jit_lp` and `liquidation` are reserved as `UnsupportedStrategy` placeholders
deferred to 3.1.3a and 3.2.1a.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from defi_sim.core.agent import Agent, DecisionContext
from defi_sim.core.types import Action, AgentId, AgentRole, AgentState, SwapAction
from defi_sim.engine.bundle import (
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
    TipAccount,
    TipPayment,
)
from defi_sim.engine.scheduler import AccountId
from defi_sim.engine.transactions import VersionedTransaction

if TYPE_CHECKING:
    from defi_sim.agents.tip_curve import TipCurveSpec


# PRD US-013 (line 1035): backrun and sandwich are first-class in Phase 1.11.
# jit_lp and liquidation are reserved as UnsupportedStrategy placeholders that
# error at JitoSearcherParams construction; they unblock alongside the
# protocol model that backs them.
_FIRST_CLASS_STRATEGIES: frozenset[str] = frozenset({"backrun", "sandwich"})
_DEFERRED_STRATEGIES: dict[str, str] = {
    "jit_lp": "3.1.3a (DLMM model)",
    "liquidation": "3.2.1a (Kamino Lend)",
}


@dataclass
class StrategyCounters:
    """Per-strategy accumulators for the searcher's tracking metrics.

    PRD US-013 line 1049: tracks tips submitted vs paid (paid only when the
    bundle landed), bundle counts (for landing rate), and realized EV (for
    tip ROI = realized_ev_lamports / tip_paid_lamports).
    """

    bundles_submitted: int = 0
    bundles_landed: int = 0
    tips_submitted_lamports: int = 0
    tips_paid_lamports: int = 0
    realized_ev_lamports: int = 0


@dataclass
class JitoSearcherMetrics:
    """Per-strategy, per-run tracking for ``JitoSearcher`` (PRD line 1049).

    Submission and landing are recorded as separate events because tips are
    only paid when the bundle lands; submitted-but-dropped bundles still
    count toward the denominator of the landing rate.
    """

    by_strategy: dict[str, StrategyCounters] = field(default_factory=dict)

    def _bucket(self, strategy: str) -> StrategyCounters:
        bucket = self.by_strategy.get(strategy)
        if bucket is None:
            bucket = StrategyCounters()
            self.by_strategy[strategy] = bucket
        return bucket

    def record_submitted(self, strategy: str, tip_lamports: int) -> None:
        bucket = self._bucket(strategy)
        bucket.bundles_submitted += 1
        bucket.tips_submitted_lamports += int(tip_lamports)

    def record_landed(
        self,
        strategy: str,
        tip_lamports: int,
        realized_ev_lamports: int,
    ) -> None:
        bucket = self._bucket(strategy)
        bucket.bundles_landed += 1
        bucket.tips_paid_lamports += int(tip_lamports)
        bucket.realized_ev_lamports += int(realized_ev_lamports)

    def landing_rate(self, strategy: str) -> float:
        bucket = self._bucket(strategy)
        if bucket.bundles_submitted == 0:
            return 0.0
        return bucket.bundles_landed / bucket.bundles_submitted

    def tip_roi(self, strategy: str) -> float:
        bucket = self._bucket(strategy)
        if bucket.tips_paid_lamports == 0:
            return 0.0
        return bucket.realized_ev_lamports / bucket.tips_paid_lamports

    def to_snapshot_dict(self, *, bundle_auction: object | None = None) -> dict[str, object]:
        """Serialize per-strategy counters for ``RoundSnapshot.metrics``.

        PRD US-013 line 1053: surfaces under
        ``metrics.jito_searcher.<agent_id>``.

        When the bound :class:`BundleAuction` carries a fitted
        :class:`TipQuoteCurve` (FIX-020), the payload includes a
        ``calibration`` block describing the corpus + capture date the
        prior was fit against. When no curve is bound, the payload still
        carries the legacy ``synthetic: True`` marker so older clients (and
        non-Solana templates that haven't been migrated to the calibrated
        path) keep their existing UI affordances.

        ``bundle_auction`` is passed by ``SimulationEngine._collect_snapshot_metrics``
        when the execution model is ``SolanaLikeExecution``. Tests / unit
        callers can pass ``None`` and the legacy marker is emitted.
        """
        by_strategy: dict[str, dict[str, float | int]] = {}
        for strategy, bucket in self.by_strategy.items():
            by_strategy[strategy] = {
                "bundles_submitted": bucket.bundles_submitted,
                "bundles_landed": bucket.bundles_landed,
                "tips_submitted_lamports": bucket.tips_submitted_lamports,
                "tips_paid_lamports": bucket.tips_paid_lamports,
                "realized_ev_lamports": bucket.realized_ev_lamports,
                "landing_rate": self.landing_rate(strategy),
                "tip_roi": self.tip_roi(strategy),
            }
        payload: dict[str, object] = {"by_strategy": by_strategy}
        curve = getattr(bundle_auction, "tip_quote_curve", None)
        if curve is not None and getattr(curve, "n_bundles", 0) > 0:
            payload["calibration"] = curve.metadata()
        else:
            payload["synthetic"] = True
        return payload


@dataclass(kw_only=True)
class JitoSearcherParams:
    strategies: list[Literal["backrun", "sandwich", "jit_lp", "liquidation"]]
    tip_curve: TipCurveSpec
    min_ev_to_submit_lamports: int
    tip_account: TipAccount
    max_bundle_size: int = 5
    priority_fee_percentile_target: int = 75
    # PRD US-009 line 666 / Phase 1.5 lighthouse: when set, every
    # ``VersionedTransaction`` the searcher emits in a bundle references
    # these ALT ids in its ``lookup_tables`` field. The engine's
    # ``compute_tx_size`` resolves each account against the configured
    # registry so on-chain-style 3-byte ALT references stand in for
    # 32-byte raw pubkeys — keeping multi-tx sandwich bundles under the
    # 1232-byte tx-size cap. Empty tuple disables ALT attachment.
    alt_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for strategy in self.strategies:
            if strategy in _FIRST_CLASS_STRATEGIES:
                continue
            if strategy in _DEFERRED_STRATEGIES:
                phase = _DEFERRED_STRATEGIES[strategy]
                raise ValueError(
                    f"strategy {strategy!r} is an UnsupportedStrategy in "
                    f"Phase 1.11; deferred to {phase}"
                )
            raise ValueError(
                f"unknown strategy {strategy!r}; expected one of "
                f"{sorted(_FIRST_CLASS_STRATEGIES | _DEFERRED_STRATEGIES.keys())}"
            )


class JitoSearcher(Agent):
    """Searcher agent that submits Jito ``Bundle``s tipping the leader.

    Note on PRD wording: PRD US-013 line 1024 phrases ``decide(...)`` as
    "emits ``BundleAction``", but Jito bundles are not Action objects in
    this engine — they are ``Bundle`` instances submitted out-of-band via
    ``SolanaLikeExecution.submit_bundle``. ``BundleAction`` is a
    pre-existing multi-asset weighted-trade Action and is unrelated.
    Bundles flow through ``DecisionContext.submit_bundle`` (US-013 wiring)
    so ``decide`` keeps its ``list[Action]`` contract.
    """

    def __init__(self, agent_id: AgentId, params: JitoSearcherParams):
        self.agent_id = agent_id
        self.params = params
        self.state = AgentState(
            agent_id=agent_id,
            role=AgentRole("jito_searcher"),
        )
        self.metrics = JitoSearcherMetrics()

    def decide(self, ctx: DecisionContext) -> list[Action]:
        """PRD US-013 line 1024: walk configured strategies, build bundles,
        and submit each via ``ctx.submit_bundle``.

        Returns an empty action list — Jito bundles are submitted via the
        side-channel rather than as ``Action`` objects since ``Bundle``
        is not an ``Action`` subtype. When no submit_bundle channel is
        attached (non-Solana execution model), the searcher is a no-op.
        """
        if ctx.submit_bundle is None:
            return []
        for strategy in self.params.strategies:
            bundle: Bundle | None = None
            if strategy == "backrun":
                bundle = self.run_backrun(ctx)
            elif strategy == "sandwich":
                bundle = self.run_sandwich(ctx)
            else:
                continue
            if bundle is None:
                continue
            ctx.submit_bundle(bundle)
            self.metrics.record_submitted(strategy, bundle.tip_lamports)
        return []

    def size_tip(
        self,
        expected_ev: int,
        ctx: DecisionContext,
        target_account: AccountId,
    ) -> int:
        """Compute a Jito tip from expected EV and the priority-fee market.

        PRD US-013 line 1042: queries
        ``ctx.priority_fee_market.quote(target_account, percentile)`` at the
        configured ``priority_fee_percentile_target`` and feeds the quote +
        EV into ``params.tip_curve.apply``. The target account is the
        pool/account the searcher is competing for write-lock priority on
        (caller-supplied because the relevant account depends on the
        strategy's victim swap, not on the searcher's static config).
        """
        fee_quote = 0
        if ctx.priority_fee_market is not None:
            fee_quote = ctx.priority_fee_market.quote(
                target_account, self.params.priority_fee_percentile_target
            )
        return self.params.tip_curve.apply(
            expected_ev=expected_ev, fee_quote=fee_quote
        )

    @staticmethod
    def _victim_notional(action: SwapAction, ctx: DecisionContext) -> float:
        """Notional value of a candidate victim swap, in quote-token units.

        The plain ``int(action.amount_in)`` compare in ``run_sandwich`` /
        ``run_backrun`` is wrong across mixed-decimal pools: SOL is 9-decimal
        and USDC is 6-decimal, so a $25 SOL swap has ``amount_in = 25e9``
        while a $25 USDC swap has ``amount_in = 25e6``. The SOL trade looks
        ~1000× larger to a raw-int compare and gets picked as the victim
        every slot — its swap then only executes inside a sandwich whose
        front+back-run cancel out, so the agent's net market impact is zero.

        This helper reads the per-token decimals the engine plumbs into
        ``ctx.extra['token_decimals']`` and the AMM's spot prices on
        ``ctx.market_state.prices`` (already decimal-adjusted, e.g. USDC
        per SOL on a SOL/USDC pool) to convert ``amount_in`` into a
        notional value in the quote token's units. Falls back to
        ``float(amount_in)`` (legacy raw compare) when either lookup is
        unavailable so non-engine callers / fixtures keep working.
        """
        market_state = ctx.market_state
        decimals = (ctx.extra or {}).get("token_decimals") if ctx.extra else None
        prices = market_state.prices if market_state is not None else None
        if not decimals or not prices:
            return float(action.amount_in)
        dec = decimals.get(action.token_in)
        price = prices.get(action.token_in)
        if dec is None or price is None or float(price) <= 0:
            return float(action.amount_in)
        return float(action.amount_in) / (10 ** int(dec)) * float(price)

    def _victim_pool_account(
        self,
        victim: SwapAction,
        ctx: DecisionContext,
    ) -> AccountId:
        """Resolve the pool write-lock account for a victim swap.

        ``size_tip`` queries the priority-fee market keyed by account; the
        relevant account is whatever the victim's swap writes (the pool).
        Falls back to a synthetic ``pool:<token_in>/<token_out>`` key when
        ``ctx.resolve_locks`` is not wired (standalone fixtures) — the fee
        market returns 0 for unknown accounts so sizing degrades to the
        EV-only curve in that case.
        """
        if ctx.resolve_locks is not None:
            resolved = ctx.resolve_locks(victim)
            if resolved is not None and resolved.write_locks:
                return next(iter(resolved.write_locks))
        return f"pool:{victim.token_in}/{victim.token_out}"

    def run_backrun(self, ctx: DecisionContext) -> Bundle | None:
        """Detect a large pending swap and emit a back-run bundle.

        PRD US-013 line 1036 (1.11 ``backrun``): scans ``ctx.pending_actions``
        for the largest ``SwapAction`` whose ``amount_in`` exceeds
        ``params.min_ev_to_submit_lamports`` (coarse proxy for "EV exceeds
        threshold" until 2.1 calibrates a real EV estimator). Builds a
        back-running swap on the same token pair plus a standalone tip-only
        tx; returns ``None`` when ``backrun`` is not configured or no victim
        clears the threshold.

        Tip sizing: PRD line 1075 — ``size_tip`` queries the priority-fee
        market at the configured percentile and feeds the quote + EV into
        ``params.tip_curve.apply``. Result is floored to
        ``MIN_BUNDLE_TIP_LAMPORTS`` to satisfy ``Bundle``'s Jito-minimum
        invariant.

        Coexistence: a back-run rides after the victim — the bundle and the
        regular-queue victim both write the pool, but the auction reserves
        the bundle first and the runtime executes it after regular trading.
        The bundle declares ``coexisting_actions=(victim,)`` so the auction's
        non-bundle conflict check exempts the victim's locks for this
        candidate (PRD US-013 line 1056).
        """
        if "backrun" not in self.params.strategies:
            return None
        pending = ctx.pending_actions or []
        candidates = [
            action
            for action in pending
            if isinstance(action, SwapAction)
            and int(action.amount_in) >= self.params.min_ev_to_submit_lamports
        ]
        if not candidates:
            return None
        victim = max(candidates, key=lambda a: self._victim_notional(a, ctx))
        expected_ev = int(victim.amount_in)
        target_account = self._victim_pool_account(victim, ctx)
        tip_lamports = max(
            self.size_tip(expected_ev, ctx, target_account),
            MIN_BUNDLE_TIP_LAMPORTS,
        )
        alt_ids_list = list(self.params.alt_ids)
        backrun_tx = VersionedTransaction(
            actions=[
                SwapAction(
                    agent_id=self.agent_id,
                    token_in=victim.token_out,
                    token_out=victim.token_in,
                    amount_in=victim.amount_in,
                    submission_path="jito_relayer",
                )
            ],
            lookup_tables=list(alt_ids_list),
        )
        tip_tx = VersionedTransaction(
            actions=[],
            lookup_tables=list(alt_ids_list),
        )
        tip_payment = TipPayment(
            tx_index=1,
            location="standalone_tx",
            lamports=tip_lamports,
            recipient=self.params.tip_account,
        )
        return Bundle(
            txs=[backrun_tx, tip_tx],
            tip_payments=[tip_payment],
            searcher_id=self.agent_id,
            strategy="backrun",
            expected_ev_lamports=expected_ev,
            coexisting_actions=(victim,),
            execute_after_regular_actions=True,
        )

    def run_sandwich(self, ctx: DecisionContext) -> Bundle | None:
        """Front-run + victim + back-run, all in one atomic bundle.

        PRD US-013 line 1037 (1.11 ``sandwich``): scans ``ctx.pending_actions``
        for the largest ``SwapAction`` whose ``amount_in`` clears
        ``params.min_ev_to_submit_lamports``; emits a 3-tx bundle of
        front-run, victim (copied verbatim), back-run. The tip rides as an
        ``instruction`` on the back-run tx so the bundle is exactly 3 txs
        (matches PRD line 1062 ``test_sandwich_strategy_emits_three_tx_bundle``).

        Returns ``None`` when ``sandwich`` is not configured or no victim
        clears the threshold.

        Tip sizing: ``size_tip`` queries the priority-fee market at the
        configured percentile (PRD US-013 line 1075). Floored to
        ``MIN_BUNDLE_TIP_LAMPORTS``.

        Consumption: a sandwich subsumes the victim's signature — the
        bundle includes a verbatim copy. The bundle declares
        ``consumed_actions=(victim,)`` so the engine drops the victim from
        the regular queue before admit (no double-execution).
        """
        if "sandwich" not in self.params.strategies:
            return None
        pending = ctx.pending_actions or []
        candidates = [
            action
            for action in pending
            if isinstance(action, SwapAction)
            and int(action.amount_in) >= self.params.min_ev_to_submit_lamports
        ]
        if not candidates:
            return None
        victim = max(candidates, key=lambda a: self._victim_notional(a, ctx))
        expected_ev = int(victim.amount_in)
        target_account = self._victim_pool_account(victim, ctx)
        tip_lamports = max(
            self.size_tip(expected_ev, ctx, target_account),
            MIN_BUNDLE_TIP_LAMPORTS,
        )
        alt_ids_list = list(self.params.alt_ids)
        front_run_tx = VersionedTransaction(
            actions=[
                SwapAction(
                    agent_id=self.agent_id,
                    token_in=victim.token_in,
                    token_out=victim.token_out,
                    amount_in=victim.amount_in,
                    submission_path="jito_relayer",
                )
            ],
            lookup_tables=list(alt_ids_list),
        )
        victim_tx = VersionedTransaction(
            actions=[victim],
            lookup_tables=list(alt_ids_list),
        )
        back_run_tx = VersionedTransaction(
            actions=[
                SwapAction(
                    agent_id=self.agent_id,
                    token_in=victim.token_out,
                    token_out=victim.token_in,
                    amount_in=victim.amount_in,
                    submission_path="jito_relayer",
                )
            ],
            lookup_tables=list(alt_ids_list),
        )
        tip_payment = TipPayment(
            tx_index=2,
            location="instruction",
            lamports=tip_lamports,
            recipient=self.params.tip_account,
        )
        return Bundle(
            txs=[front_run_tx, victim_tx, back_run_tx],
            tip_payments=[tip_payment],
            searcher_id=self.agent_id,
            strategy="sandwich",
            expected_ev_lamports=expected_ev,
            consumed_actions=(victim,),
        )
