/**
 * Wrapper over `GET /runs/{id}/metrics/{metric}` — per-round series from
 * the pre-aggregated `round_metrics` table. Used for the scrubber and
 * any post-paint metric drill-downs; the results-page initial paint
 * gets these slices bundled inside the overview view.
 */

import { apiFetch } from "@/lib/api/client";
import type { OverviewSeriesPoint } from "@/lib/services/runViewService";

// The backend currently exposes these four columns out of `round_metrics`
// (see `_QUERYABLE_METRICS` in `src/defi_sim_api/backend/pg_store.py`).
// Adding a metric is an explicit code change on both ends.
export type QueryableMetric =
  | "volume"
  | "num_actions"
  | "num_failed"
  | "gas_spent";

export interface FetchMetricSeriesParams {
  // `undefined` selects the whole-market rollup; a string targets one agent.
  agentId?: string;
  fromRound?: number;
  toRound?: number;
}

export interface MetricSeries {
  runId: string;
  metric: QueryableMetric;
  agentId: string | null;
  from: number | null;
  to: number | null;
  series: OverviewSeriesPoint[];
}

interface ApiMetricSeriesResponse {
  run_id: string;
  metric: string;
  agent_id: string | null;
  from: number | null;
  to: number | null;
  series: OverviewSeriesPoint[];
}

export const metricsService = {
  async fetchSeries(
    runId: string,
    metric: QueryableMetric,
    params: FetchMetricSeriesParams = {},
  ): Promise<MetricSeries> {
    const resp = await apiFetch<ApiMetricSeriesResponse>(
      `/runs/${runId}/metrics/${metric}`,
      {
        query: {
          agent: params.agentId,
          from: params.fromRound,
          to: params.toRound,
        },
      },
    );
    return {
      runId: resp.run_id,
      metric: resp.metric as QueryableMetric,
      agentId: resp.agent_id,
      from: resp.from,
      to: resp.to,
      series: resp.series,
    };
  },
};
