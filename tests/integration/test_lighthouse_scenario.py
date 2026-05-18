"""Phase 1.5.1 lighthouse scenario end-to-end smoke test.

PRD ref: solana-plans/phase-1.5.md, US-001 acceptance criteria.

The lighthouse template wires every Phase 1 mechanic on the critical
path: a JitoSearcher running the sandwich strategy, a bundle auction with
``max_bundles_per_slot=5``, the priority-fee market warmed by mixed
cu-price flow, ALT compression on the seeded reference set, and a
tightened compute-budget that forces both per-slot and per-writable-account
exhaustion. All four required events from PRD US-001 lines 63-68 are
asserted unconditionally on a 500-slot run.

Mapping from PRD wording to engine events:

* "bundle landed"                  → :class:`EventType.BUNDLE_TIP_PAID`
* "bundle reverted on partial      → ``bundle_outcomes[*].status == "reverted"``
   failure (tip-position             on the per-round snapshot ledger
   semantics from 1.7)"              (``BUNDLE_TIP_REVERTED`` only fires on
                                     fork reorg today)
* "CU-budget event"                → :class:`EventType.COMPUTE_BUDGET_EXHAUSTED`
* "priority_fee_market_updated"    → :class:`EventType.PRIORITY_FEE_MARKET_UPDATED`
"""

from __future__ import annotations

import copy
import time

from defi_sim.engine.api import build_engine
from defi_sim.engine.events import EventBus, EventType
from defi_sim_api.backend.templates import find_template

LIGHTHOUSE_TEMPLATE_ID = "solana-sandwich-lighthouse"
LIGHTHOUSE_NUM_SLOTS = 500
LIGHTHOUSE_RUNTIME_BUDGET_S = 30.0


def test_lighthouse_runs_end_to_end() -> None:
    template = find_template(LIGHTHOUSE_TEMPLATE_ID)
    assert template is not None, (
        f"lighthouse template {LIGHTHOUSE_TEMPLATE_ID!r} missing from catalog"
    )

    spec = copy.deepcopy(template["base_spec"])
    spec["num_rounds"] = LIGHTHOUSE_NUM_SLOTS

    bus = EventBus(record_history=True)
    engine = build_engine(spec, event_bus=bus)

    # PRD US-001 line 58: "Priority fee market warmed over a 200-slot
    # pre-roll so the distribution percentiles are non-degenerate."
    # Verify *before* the run that the seeded pool account already
    # has a non-collapsed distribution — without this the lighthouse
    # JitoSearcher would quote the engine floor for the first ~150
    # slots and EV math would be degenerate.
    pfm = engine.priority_fee_market
    assert pfm is not None, "lighthouse template must wire a priority_fee_market"
    # Real SOL/USDC Whirlpool pubkey — matches the pool the corpus fixture
    # is forked against (slot 420196842, 4 bps tier) and the pre-roll's
    # `accounts` list.
    pre_roll_account = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
    percentiles = pfm.percentiles(pre_roll_account)
    assert percentiles[25] > 1, (
        f"pre-roll did not seed pool account {pre_roll_account!r} — got "
        f"p25={percentiles[25]} (engine floor=1). PRD US-001 line 58 "
        "requires a non-degenerate distribution at slot 0."
    )
    assert percentiles[99] > percentiles[25], (
        "pre-roll distribution collapsed to a single value — PRD US-001 "
        f"line 58 requires non-degenerate percentiles, got {percentiles}"
    )

    start = time.perf_counter()
    engine.run()
    elapsed = time.perf_counter() - start
    assert elapsed < LIGHTHOUSE_RUNTIME_BUDGET_S, (
        f"lighthouse {LIGHTHOUSE_NUM_SLOTS}-slot run took {elapsed:.2f}s, "
        f"PRD US-001 budget is {LIGHTHOUSE_RUNTIME_BUDGET_S:.0f}s on a dev laptop"
    )

    # PRD US-001 selection criterion #1: the lighthouse must exercise the
    # Solana slot clock + leader schedule (1.1). If the spec drops the
    # ``clock`` key the engine silently falls back to BlockClock and
    # current_slot/current_leader stay None on every snapshot. Assert
    # against the final snapshot so a regression of the clock spec fails
    # this test instead of slipping past the DoD.
    last_snapshot = engine._snapshots[-1]
    assert last_snapshot.current_slot is not None, (
        "lighthouse final snapshot has current_slot=None — PRD US-001 "
        "criterion #1 requires the Solana slot clock; the spec must wire "
        "clock={type: 'solana_slot', ...}"
    )
    assert last_snapshot.current_slot >= LIGHTHOUSE_NUM_SLOTS - 1, (
        f"lighthouse final snapshot reports current_slot="
        f"{last_snapshot.current_slot}, expected >= "
        f"{LIGHTHOUSE_NUM_SLOTS - 1} after a {LIGHTHOUSE_NUM_SLOTS}-slot run"
    )
    assert last_snapshot.current_leader is not None, (
        "lighthouse final snapshot has current_leader=None — PRD US-001 "
        "criterion #1 requires the leader schedule to resolve a leader "
        "for current_slot"
    )

    history = bus.history
    round_ends = [e for e in history if e.type is EventType.ROUND_END]
    assert len(round_ends) == LIGHTHOUSE_NUM_SLOTS, (
        f"expected {LIGHTHOUSE_NUM_SLOTS} ROUND_END events, got {len(round_ends)}"
    )
    sim_ends = [e for e in history if e.type is EventType.SIMULATION_END]
    assert len(sim_ends) == 1, "expected exactly one SIMULATION_END event"

    landed_events = [e for e in history if e.type is EventType.BUNDLE_TIP_PAID]
    cu_budget_events = [
        e for e in history if e.type is EventType.COMPUTE_BUDGET_EXHAUSTED
    ]
    fee_market_events = [
        e for e in history if e.type is EventType.PRIORITY_FEE_MARKET_UPDATED
    ]
    submission_drops = [e for e in history if e.type is EventType.ACTION_DROPPED]

    landed_outcomes = 0
    reverted_outcomes = 0
    dropped_outcomes = 0
    for snapshot in engine._snapshots:
        for outcome in getattr(snapshot, "bundle_outcomes", None) or []:
            status = getattr(outcome, "status", None)
            if status == "landed":
                landed_outcomes += 1
            elif status == "reverted":
                reverted_outcomes += 1
            elif status == "dropped":
                dropped_outcomes += 1

    # PRD US-001 line 64: "At least one bundle landed."
    assert landed_events, (
        "expected at least one BUNDLE_TIP_PAID event (landed bundle) in "
        f"{LIGHTHOUSE_NUM_SLOTS} slots — the lighthouse JitoSearcher must "
        "successfully sandwich at least one victim"
    )
    assert landed_outcomes == len(landed_events), (
        f"snapshot bundle_outcomes 'landed' count ({landed_outcomes}) must "
        f"match BUNDLE_TIP_PAID event count ({len(landed_events)}) — both "
        "derive from the same per-slot ledger"
    )

    # PRD US-001 line 66: "At least one bundle was reverted on partial failure
    # (tip-position semantics from 1.7)." Partial-failure reverts surface on
    # the snapshot ledger as bundle_outcomes[*].status == "reverted".
    assert reverted_outcomes > 0, (
        "expected at least one bundle to revert on partial failure in "
        f"{LIGHTHOUSE_NUM_SLOTS} slots — searcher inventory drains over "
        "many sandwich attempts so the back-run leg eventually fails, "
        "which exercises the tip-position 'instruction-location tip is "
        "lost on revert' semantics from PRD US-011"
    )

    # PRD US-001 line 67: "At least one CU-budget event was emitted."
    assert cu_budget_events, (
        f"expected at least one COMPUTE_BUDGET_EXHAUSTED event in "
        f"{LIGHTHOUSE_NUM_SLOTS} slots — the lighthouse spec sets "
        "per_writable_account=600_000 against a hot CFAMM pool to force "
        "this"
    )

    # PRD US-001 line 68: "At least one priority_fee_market_updated event was
    # emitted on the hot pool."
    assert fee_market_events, (
        f"expected at least one PRIORITY_FEE_MARKET_UPDATED event in "
        f"{LIGHTHOUSE_NUM_SLOTS} slots — swap_noise victims observe with "
        "varied cu_prices so percentiles shift past the configured "
        "update_event_threshold"
    )

    # PRD US-001 line 74: run snapshot must contain submission-path drops.
    assert submission_drops, (
        "expected at least one ACTION_DROPPED (submission-path drop) event "
        f"in {LIGHTHOUSE_NUM_SLOTS} slots — the default RPC submission "
        "prior is 0.85 so ~15% of regular traffic should drop"
    )

    # PRD US-001 line 74: run snapshot must contain JitoSearcher metrics.
    # FIX-020: the lighthouse template wires the fitted TipQuoteCurve so
    # the per-searcher payload now carries a ``calibration`` metadata
    # block (source / captured_at / n_bundles / n_slots) instead of the
    # legacy ``synthetic: True`` marker. The final snapshot is the
    # canonical surface — landing rate / tip ROI live under
    # ``metrics.jito_searcher.<agent_id>``.
    last_snapshot = engine._snapshots[-1]
    metrics = getattr(last_snapshot, "metrics", None) or {}
    jito_metrics = metrics.get("jito_searcher", {})
    assert jito_metrics, (
        "expected metrics.jito_searcher.<id> on the final snapshot — "
        "PRD US-013 line 1053 wires the landing rate / tip ROI here"
    )
    for searcher_id, payload in jito_metrics.items():
        assert payload.get("synthetic") is not True, (
            f"JitoSearcher metrics for {searcher_id!r} carry "
            "synthetic=True; FIX-020 wired a calibrated TipQuoteCurve so "
            "the payload should expose a `calibration` metadata block "
            "instead. Did the lighthouse template lose its "
            "tip_quote_curve_path?"
        )
        calibration = payload.get("calibration")
        assert isinstance(calibration, dict), (
            f"JitoSearcher {searcher_id!r} missing `calibration` block — "
            f"got payload keys {sorted(payload.keys())}"
        )
        assert calibration.get("captured_at"), (
            f"JitoSearcher {searcher_id!r} calibration block missing "
            "`captured_at`; the loaded TipQuoteCurve must carry an "
            "ISO-8601 capture timestamp"
        )
        assert int(calibration.get("n_bundles") or 0) > 0, (
            f"JitoSearcher {searcher_id!r} calibration block reports "
            "zero captured bundles — the YAML at "
            "solana-plans/calibration/jito_tip_curves.yaml must be "
            "fitted against a non-empty corpus"
        )
        by_strategy = payload.get("by_strategy") or {}
        assert by_strategy, (
            f"JitoSearcher {searcher_id!r} reported no per-strategy "
            "counters — expected at least one strategy bucket"
        )
        for strategy, counters in by_strategy.items():
            assert counters["bundles_submitted"] > 0, (
                f"searcher {searcher_id!r} strategy {strategy!r} "
                "submitted zero bundles"
            )
