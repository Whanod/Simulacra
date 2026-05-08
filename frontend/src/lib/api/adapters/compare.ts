import type { MetricDelta } from "@/lib/types/compare";

// ── Backend shape ─────────────────────────────────────────────────────────

export interface ApiCompareDiffEntry {
  left: unknown;
  right: unknown;
}

export interface ApiCompareMetricEntry {
  left: number | boolean | null | undefined;
  right: number | boolean | null | undefined;
  delta: number | null | undefined;
}

export interface ApiComparePriceDelta {
  left: { start?: number; end?: number; delta?: number };
  right: { start?: number; end?: number; delta?: number };
  delta_end: number | null | undefined;
}

export interface ApiCompareAgentDelta {
  left: { realized_pnl?: number; cumulative_volume?: number; balance_total?: number };
  right: { realized_pnl?: number; cumulative_volume?: number; balance_total?: number };
  delta_realized_pnl: number | null | undefined;
}

export interface ApiCompareResponse {
  left_run_id: string;
  right_run_id: string;
  equal: boolean;
  spec_diff: Record<string, ApiCompareDiffEntry>;
  metric_diff: Record<string, ApiCompareMetricEntry>;
  metadata_diff?: Record<string, ApiCompareDiffEntry>;
  price_summary_delta: Record<string, ApiComparePriceDelta>;
  agent_summary_delta: Record<string, ApiCompareAgentDelta>;
}

// ── Frontend shape ────────────────────────────────────────────────────────

export interface SpecDiffRow {
  key: string;
  left: unknown;
  right: unknown;
}

export interface PriceSummaryRow {
  key: string;
  leftStart?: number;
  leftEnd?: number;
  rightStart?: number;
  rightEnd?: number;
  deltaEnd?: number;
}

export interface AgentSummaryRow {
  agentId: string;
  leftPnl?: number;
  rightPnl?: number;
  deltaPnl?: number;
}

export interface CompareView {
  leftRunId: string;
  rightRunId: string;
  equal: boolean;
  specDiff: SpecDiffRow[];
  metricDeltas: MetricDelta[];
  priceSummary: PriceSummaryRow[];
  agentSummary: AgentSummaryRow[];
}

const HIGHER_IS_BETTER = new Set(["num_rounds_executed"]);
const LOWER_IS_BETTER = new Set(["stopped_early", "cancelled"]);

function classifyMetric(metric: string, delta: number): MetricDelta["direction"] {
  if (delta === 0) return "neutral";
  if (HIGHER_IS_BETTER.has(metric)) return delta > 0 ? "better" : "worse";
  if (LOWER_IS_BETTER.has(metric)) return delta < 0 ? "better" : "worse";
  return "neutral";
}

function valueToNumber(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  return 0;
}

export function fromApiCompare(raw: ApiCompareResponse): CompareView {
  const specDiff: SpecDiffRow[] = Object.entries(raw.spec_diff || {}).map(
    ([key, entry]) => ({ key, left: entry.left, right: entry.right }),
  );

  const metricDeltas: MetricDelta[] = Object.entries(raw.metric_diff || {}).map(
    ([metric, entry]) => {
      const valueA = valueToNumber(entry.left);
      const valueB = valueToNumber(entry.right);
      const delta = typeof entry.delta === "number" ? entry.delta : valueB - valueA;
      return {
        metric,
        valueA,
        valueB,
        delta,
        direction: classifyMetric(metric, delta),
      };
    },
  );

  const priceSummary: PriceSummaryRow[] = Object.entries(
    raw.price_summary_delta || {},
  ).map(([key, entry]) => ({
    key,
    leftStart: entry.left?.start,
    leftEnd: entry.left?.end,
    rightStart: entry.right?.start,
    rightEnd: entry.right?.end,
    deltaEnd: typeof entry.delta_end === "number" ? entry.delta_end : undefined,
  }));

  const agentSummary: AgentSummaryRow[] = Object.entries(
    raw.agent_summary_delta || {},
  )
    .map(([agentId, entry]) => ({
      agentId,
      leftPnl: entry.left?.realized_pnl,
      rightPnl: entry.right?.realized_pnl,
      deltaPnl:
        typeof entry.delta_realized_pnl === "number"
          ? entry.delta_realized_pnl
          : undefined,
    }))
    .sort((a, b) => Math.abs(b.deltaPnl ?? 0) - Math.abs(a.deltaPnl ?? 0));

  return {
    leftRunId: raw.left_run_id,
    rightRunId: raw.right_run_id,
    equal: !!raw.equal,
    specDiff,
    metricDeltas,
    priceSummary,
    agentSummary,
  };
}
