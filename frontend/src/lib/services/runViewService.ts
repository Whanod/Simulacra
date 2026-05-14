/**
 * View-bundle service for the postgres-backed `/runs/{id}/views/*` endpoints.
 *
 * Each view bundles the data one page needs into a single round trip;
 * granular post-paint fetches go through `metricsService` / `eventsService`.
 * See `docs/postgres-migration-plan.md` Phase 4 for the view-vs-resource
 * split and the rationale.
 */

import { apiFetch } from "@/lib/api/client";
import type { ApiRun, ApiRunResult } from "@/lib/api/adapters/runs";

export interface OverviewSpecSummary {
  market_type: string | null;
  agent_types: string[];
  num_rounds: number | null;
  seed: number | null;
}

export interface OverviewSeriesPoint {
  round: number;
  // NUMERIC columns round-trip as numbers; null when the engine didn't
  // emit the row (e.g. a metric column that's still being added).
  value: number | null;
}

export interface OverviewEventSummaryEntry {
  type: string;
  count: number;
}

export type OverviewSeriesMetric =
  | "volume"
  | "num_actions"
  | "num_failed"
  | "gas_spent";

// Phase 4.5 chart slices. Five reproduce numeric shapes off the legacy
// `result` payload; `fee_history` is the only slice served from a
// dedicated table (`fees`) rather than a JSONB pluck. All slices are
// optional/nullable because the engine doesn't populate every field for
// every template — chart adapters already default to `[]` via `|| []`,
// so the wire shape stays permissive.
type WhirlpoolSnapshotsBundle = Array<{
  round: number | null;
  whirlpool: Record<string, unknown> | null;
}>;

export interface OverviewView {
  run: ApiRun;
  spec_summary: OverviewSpecSummary;
  // Engine-derived metrics — same shape `RecommendedMetricsGrid`
  // consumes today, just delivered via the view instead of
  // `run.metadata.derived_metrics`. Backend (routers/runs.py:372-376)
  // drops bools and NaN but preserves `Infinity` as a sentinel (e.g.
  // `fees_vs_il_breakeven`); JSON encodes that as `null` on the wire,
  // so values land as `number | null` here.
  tiles: Record<string, number | null>;
  series: Record<OverviewSeriesMetric, OverviewSeriesPoint[]>;
  event_summary: OverviewEventSummaryEntry[];
  // Chart slices, each backed by a typed column on `runs` (Phase 5.1).
  // Types mirror `ApiRunResult` so the adapters that read off the view
  // bundle stay one-line shifts away from the legacy shape.
  //
  // `volume_history` / `liquidity_history` are intentionally absent —
  // the engine never populated them; the legacy view bundle carried them
  // as always-null keys. Phase 5.2 dropped them; consumers fall back to
  // whirlpool / round-snapshot derivations as before.
  price_history: ApiRunResult["price_history"] | null;
  agent_final_states: ApiRunResult["agent_final_states"] | null;
  whirlpool_snapshots: WhirlpoolSnapshotsBundle | null;
  // Sandwich totals lifted off `result.metadata.sandwich_*`. The backend
  // picks only the three keys the StressScore / bundle counters read,
  // so the surface stays fixed-shape across future metadata additions.
  sandwich_summary: {
    sandwich_bundles_landed?: number;
    sandwich_bundles_submitted?: number;
    sandwich_realized_ev_lamports?: number;
  } | null;
  // Per-metric ErrorBand passthrough from top-level `result.replay_diff`
  // (where `persist_replay_run` writes it). Shape-opaque on the wire to
  // avoid pinning the engine's calibration format here — the calibration
  // UI re-types it at the call site (see `calibrationBands.ts:113`).
  replay_diff: Record<string, unknown> | null;
  fee_history: ApiRunResult["fee_history"];
  // Phase 4 page-rewire: server-side aggregations of the four
  // `result.round_snapshots` surfaces the results page used to derive
  // client-side. `null` whenever the run never produced that data (e.g.
  // non-Solana templates emit null Solana/bundle/searcher summaries).
  num_rounds_executed: number | null;
  solana_slot_summary: {
    current_slot: number | null;
    current_leader: string | null;
  } | null;
  bundle_outcomes_summary: {
    counts: { landed: number; reverted: number; dropped: number };
    timeline: {
      landed: number[];
      reverted: number[];
      dropped: number[];
    };
    tips_paid_lamports: number;
    drop_reasons: Record<string, number>;
    landing_rate_stats: {
      avg: number;
      stdev: number;
      rounds_with_bundles: number;
    };
  } | null;
  jito_searcher_summary: {
    bundles_submitted: number;
    bundles_landed: number;
    tips_submitted_lamports: number;
    tips_paid_lamports: number;
    realized_ev_lamports: number;
    landing_rate: number;
    tip_roi: number;
    synthetic: boolean;
    // Calibration metadata is shape-opaque on the wire — the page renders
    // a quiet footer keyed off the presence of known fields without
    // pinning the format here.
    calibration: Record<string, unknown> | null;
  } | null;
  // Latest non-null `metrics.replay` payload across the run. Shape-opaque;
  // the replay UI re-types at the call site.
  replay_metrics: Record<string, unknown> | null;
}

export const runViewService = {
  async fetchOverview(runId: string): Promise<OverviewView> {
    return apiFetch<OverviewView>(`/runs/${runId}/views/overview`);
  },
};
