/**
 * Query-key conventions for @tanstack/react-query.
 *
 * Centralized so invalidation stays surgical: hitting `runs.detail(id)`
 * invalidates everything nested under that run (view, snapshot, events…)
 * without sweeping the whole cache.
 *
 * Shape rule: `[domain, id, subdomain, ...params]`. Match the migration
 * plan's Phase 4 example: `['run', runId, 'view', 'overview']`.
 */

export const queryKeys = {
  runs: {
    all: ["run"] as const,
    detail: (runId: string) => ["run", runId] as const,
    view: (runId: string, viewName: "overview" | "agent" | "compare") =>
      ["run", runId, "view", viewName] as const,
    agentView: (runId: string, agentId: string) =>
      ["run", runId, "view", "agent", agentId] as const,
    metric: (
      runId: string,
      metric: string,
      params?: Record<string, string | number | undefined>,
    ) => ["run", runId, "metric", metric, params ?? {}] as const,
    events: (
      runId: string,
      params?: Record<string, string | number | undefined>,
    ) => ["run", runId, "events", params ?? {}] as const,
    snapshot: (runId: string, round: number) =>
      ["run", runId, "snapshot", round] as const,
    correlation: (runId: string, correlationId: string) =>
      ["run", runId, "correlation", correlationId] as const,
  },
  sweeps: {
    all: ["sweep"] as const,
    detail: (sweepId: string) => ["sweep", sweepId] as const,
  },
  reports: {
    all: ["report"] as const,
    detail: (reportId: string) => ["report", reportId] as const,
  },
} as const;
