/**
 * react-query bindings for the postgres-backed run endpoints.
 *
 * Keep the typed hooks here rather than ad-hoc `useQuery` calls in pages
 * so the query-key convention from `@/lib/api/queryKeys` stays the single
 * source of truth for invalidation. See `docs/postgres-migration-plan.md`
 * Phase 4 for the view-vs-resource split and the live-vs-terminal stale
 * policy.
 */

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import { queryKeys } from "@/lib/api/queryKeys";
import { apiFetch } from "@/lib/api/client";
import { fromApiRun, type ApiRun } from "@/lib/api/adapters/runs";
import type { SimRun } from "@/lib/types";
import {
  eventsService,
  type CorrelationChain,
  type EventsPage,
  type FetchEventsParams,
} from "@/lib/services/eventsService";
import {
  metricsService,
  type FetchMetricSeriesParams,
  type MetricSeries,
  type QueryableMetric,
} from "@/lib/services/metricsService";
import {
  runViewService,
  type OverviewView,
} from "@/lib/services/runViewService";

// Terminal data (events, completed-run views) is content-addressed by
// `(run_id, …filters)` and never mutates — once we have it, it's stable.
// Pages can opt into a `refetchInterval` per usage when wired against a
// live-running run (status === "running"); the default policy keeps the
// scrubber responsive without re-fetching while a user clicks around.
const TERMINAL_STALE_MS = Number.POSITIVE_INFINITY;

type Options<T> = Omit<UseQueryOptions<T>, "queryKey" | "queryFn">;

export function useRun(runId: string | undefined, options?: Options<SimRun>) {
  return useQuery<SimRun>({
    queryKey: runId ? queryKeys.runs.detail(runId) : ["run", "__missing__"],
    queryFn: async () => {
      const raw = await apiFetch<ApiRun>(`/runs/${runId}`);
      return fromApiRun(raw);
    },
    enabled: Boolean(runId),
    ...options,
  });
}

export function useRunOverview(
  runId: string | undefined,
  options?: Options<OverviewView>,
) {
  return useQuery<OverviewView>({
    queryKey: runId
      ? queryKeys.runs.view(runId, "overview")
      : ["run", "__missing__", "view", "overview"],
    queryFn: () => runViewService.fetchOverview(runId as string),
    enabled: Boolean(runId),
    staleTime: TERMINAL_STALE_MS,
    ...options,
  });
}

export function useRunMetric(
  runId: string | undefined,
  metric: QueryableMetric,
  params: FetchMetricSeriesParams = {},
  options?: Options<MetricSeries>,
) {
  const keyParams: Record<string, string | number | undefined> = {
    agent: params.agentId,
    from: params.fromRound,
    to: params.toRound,
  };
  return useQuery<MetricSeries>({
    queryKey: runId
      ? queryKeys.runs.metric(runId, metric, keyParams)
      : ["run", "__missing__", "metric", metric, keyParams],
    queryFn: () => metricsService.fetchSeries(runId as string, metric, params),
    enabled: Boolean(runId),
    staleTime: TERMINAL_STALE_MS,
    ...options,
  });
}

export function useRunEvents(
  runId: string | undefined,
  params: FetchEventsParams = {},
  options?: Options<EventsPage>,
) {
  const keyParams: Record<string, string | number | undefined> = {
    event_type: params.eventType,
    agent_id: params.agentId,
    round: params.round,
    from: params.fromRound,
    to: params.toRound,
    cursor: params.cursor,
    limit: params.limit,
    offset: params.offset,
  };
  return useQuery<EventsPage>({
    queryKey: runId
      ? queryKeys.runs.events(runId, keyParams)
      : ["run", "__missing__", "events", keyParams],
    queryFn: () => eventsService.fetchEvents(runId as string, params),
    enabled: Boolean(runId),
    staleTime: TERMINAL_STALE_MS,
    ...options,
  });
}

export function useRunCorrelation(
  runId: string | undefined,
  correlationId: string | undefined,
  options?: Options<CorrelationChain>,
) {
  return useQuery<CorrelationChain>({
    queryKey:
      runId && correlationId
        ? queryKeys.runs.correlation(runId, correlationId)
        : ["run", "__missing__", "correlation", correlationId ?? ""],
    queryFn: () =>
      eventsService.fetchCorrelation(runId as string, correlationId as string),
    enabled: Boolean(runId && correlationId),
    staleTime: TERMINAL_STALE_MS,
    ...options,
  });
}

interface ApiRunRoundResponse {
  run_id: string;
  snapshot: Record<string, unknown> | null;
}

export interface RoundSnapshot {
  runId: string;
  round: number;
  state: Record<string, unknown> | null;
}

export function useRunRound(
  runId: string | undefined,
  round: number | undefined,
  options?: Options<RoundSnapshot>,
) {
  return useQuery<RoundSnapshot>({
    queryKey:
      runId && round !== undefined
        ? queryKeys.runs.snapshot(runId, round)
        : ["run", "__missing__", "snapshot", round ?? -1],
    queryFn: async () => {
      const resp = await apiFetch<ApiRunRoundResponse>(
        `/runs/${runId}/rounds/${round}`,
      );
      return {
        runId: resp.run_id,
        round: round as number,
        state: resp.snapshot,
      };
    },
    enabled: Boolean(runId) && round !== undefined,
    staleTime: TERMINAL_STALE_MS,
    ...options,
  });
}
