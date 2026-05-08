"""Jito bundle dataclass + invariants.

A ``Bundle`` is an ordered list of ``VersionedTransaction``s that lands all-or-
nothing through Jito's auction. The tip is paid by one or more
``TipPayment``s that live INSIDE the bundle (either a standalone tip-only tx
or an instruction within any tx, including a CPI-issued one); position is
load-bearing because a revert at any later position rolls the tip back too.

See PRD US-011 (line 797) for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from defi_sim.core.types import DEFAULT_CU_LIMIT_FALLBACK
from defi_sim.engine.transactions import VersionedTransaction

if TYPE_CHECKING:
    from defi_sim.core.types import Action, AgentId

# PRD US-011 line 799: Jito enforces a hard cap of 5 transactions per bundle.
MAX_BUNDLE_TXS = 5

# PRD US-011 line 800: Jito-enforced minimum total tip across the bundle
# (currently 1_000 lamports). Surfaced as a configurable engine constant; the
# auction's __init__ may override it for calibration scenarios.
MIN_BUNDLE_TIP_LAMPORTS = 1_000

TipAccount = str


@dataclass
class TipPayment:
    """Where in the bundle the tip-transfer lives."""

    tx_index: int
    location: Literal["standalone_tx", "instruction"]
    lamports: int
    recipient: TipAccount


@dataclass
class Bundle:
    txs: list[VersionedTransaction]
    tip_payments: list[TipPayment] = field(default_factory=list)
    # PRD US-013 line 1049: searcher attribution. Set by ``JitoSearcher`` so
    # ``BundleTipPaidEvent`` and the simulation's landing-rate credit path can
    # route back to the right agent. Direct ``execution.submit_bundle`` callers
    # leave these unset.
    searcher_id: "AgentId | None" = None
    strategy: str | None = None
    # Searcher's expected EV (lamports) at submission time. Used as a synthetic
    # ``realized_ev_lamports`` when the bundle lands — until 2.1 calibrates a
    # real EV estimator, expected==realized is the synthetic rule.
    expected_ev_lamports: int = 0
    # Actions from the regular queue this bundle SUBSUMES (sandwich folds the
    # victim sig into the bundle). Engine drops these from ``slot_pending``
    # before admit so they don't double-execute.
    consumed_actions: tuple["Action", ...] = field(default_factory=tuple)
    # Actions this bundle COEXISTS with (back-run rides alongside the victim;
    # the runtime serializes them by lock). Their resolved locks are exempt
    # from the auction's non-bundle-conflict check for THIS candidate.
    coexisting_actions: tuple["Action", ...] = field(default_factory=tuple)
    # Back-run bundles must execute after the regular victim transaction. The
    # auction still selects and reserves CU before the regular scheduler so
    # slot-cap accounting is deterministic, but execution is deferred until
    # after the trading phase.
    execute_after_regular_actions: bool = False

    @property
    def tip_lamports(self) -> int:
        return sum(tp.lamports for tp in self.tip_payments)

    @property
    def tip_recipient(self) -> TipAccount | None:
        """Single recipient when every TipPayment shares one address (PRD line 814).

        Returns None when there are no tip payments, or when payments split
        across multiple recipient addresses — the caller must then walk
        ``tip_payments`` directly.
        """
        if not self.tip_payments:
            return None
        first = self.tip_payments[0].recipient
        for tp in self.tip_payments[1:]:
            if tp.recipient != first:
                return None
        return first

    @property
    def total_cu(self) -> int:
        # PRD US-011 line 840 + US-013: bundle CU drives slot/account-CU
        # reservation and tip-density ranking. An inner action without an
        # explicit ``compute_unit_limit`` must charge the same default the
        # admit path uses (``DEFAULT_CU_LIMIT_FALLBACK``); otherwise
        # searcher-emitted bundles whose swaps omit the limit (jito_searcher
        # back-run/sandwich) would tally as zero CU and bypass the cap.
        total = 0
        for tx in self.txs:
            for action in tx.actions:
                cu = getattr(action, "compute_unit_limit", None)
                total += DEFAULT_CU_LIMIT_FALLBACK if cu is None else int(cu)
        return total

    def __post_init__(self) -> None:
        if len(self.txs) > MAX_BUNDLE_TXS:
            raise ValueError(
                f"bundle exceeds Jito max of {MAX_BUNDLE_TXS} transactions"
            )
        if self.tip_lamports < MIN_BUNDLE_TIP_LAMPORTS:
            raise ValueError(
                f"bundle tip {self.tip_lamports} below Jito minimum "
                f"{MIN_BUNDLE_TIP_LAMPORTS}"
            )
        for tp in self.tip_payments:
            if not 0 <= tp.tx_index < len(self.txs):
                raise ValueError("tip_payment.tx_index out of range")

    def paid_tip_payments(
        self,
        *,
        reverted: bool,
        failed_at_index: int | None = None,
    ) -> list[TipPayment]:
        """Return tip payments that actually credit the recipient.

        PRD US-011 lines 838 / 867 — tip-position semantics: each
        ``TipPayment`` is paid only if every tx at index ``>= tp.tx_index``
        succeeds. Because the bundle is atomic, any revert at position ``j``
        rolls back all state mutations from positions ``0..j`` (including
        any tip transfer whose tx already executed). Tips at positions on
        or after ``j`` are reverted with the bundle. In both shapes the
        outcome is the same: no tip is credited when the bundle reverts.

        ``failed_at_index`` is accepted for symmetry with
        ``BundleExecutionResult`` but is not required for the decision —
        ``reverted`` alone determines whether tips land. The argument is
        retained so a future non-atomic execution mode (e.g. partial-bundle
        landing) could specialize the rule per tip without changing the
        signature.
        """
        del failed_at_index
        if reverted:
            return []
        return list(self.tip_payments)
