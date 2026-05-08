"use client";

export type ReplayMetricKey =
  | "bundle_landing_rate"
  | "tip_efficiency"
  | "slot_inclusion_latency"
  | "cu_per_dollar_tip_breakeven"
  | "skip_rate_cost"
  | "write_lock_heatmap"
  | "submission_path_comparison";

export interface ReplayMetricResult {
  value: number;
  unit: string;
  sample_size: number;
}

export interface SlotInclusionLatencyMetric extends ReplayMetricResult {
  mean?: number;
  median?: number;
  p95?: number;
  p99?: number;
  samples?: number[];
}

export interface CuTipBreakEvenMetric extends ReplayMetricResult {
  tips?: number[];
  extracted_values?: number[];
  ratios?: number[];
}

export interface WriteLockHeatmapMetric extends ReplayMetricResult {
  accounts?: string[];
  slots?: number[];
  counts?: Array<{ account: string; slot: number; count: number }>;
  max_contention?: number;
}

export interface SubmissionPathComparisonMetric extends ReplayMetricResult {
  paths?: string[];
  submitted?: number[];
  landed?: number[];
  landing_rates?: number[];
  spread?: number;
}

export interface ReplayMetrics {
  bundle_landing_rate?: ReplayMetricResult;
  tip_efficiency?: ReplayMetricResult;
  slot_inclusion_latency?: SlotInclusionLatencyMetric;
  cu_per_dollar_tip_breakeven?: CuTipBreakEvenMetric;
  skip_rate_cost?: ReplayMetricResult;
  write_lock_heatmap?: WriteLockHeatmapMetric;
  submission_path_comparison?: SubmissionPathComparisonMetric;
}

export const REPLAY_METRIC_ORDER: ReplayMetricKey[] = [
  "bundle_landing_rate",
  "tip_efficiency",
  "slot_inclusion_latency",
  "cu_per_dollar_tip_breakeven",
  "skip_rate_cost",
  "write_lock_heatmap",
  "submission_path_comparison",
];

export const REPLAY_METRIC_LABELS: Record<ReplayMetricKey, string> = {
  bundle_landing_rate: "Bundle landing rate",
  tip_efficiency: "Tip efficiency",
  slot_inclusion_latency: "Slot inclusion latency",
  cu_per_dollar_tip_breakeven: "CU/$ tip break-even",
  skip_rate_cost: "Skip-rate cost",
  write_lock_heatmap: "Write-lock contention",
  submission_path_comparison: "Submission path comparison",
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.filter(isFiniteNumber) : [];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function metricBase(raw: unknown): ReplayMetricResult | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const record = raw as Record<string, unknown>;
  return {
    value: isFiniteNumber(record.value) ? record.value : 0,
    unit: stringValue(record.unit, "count"),
    sample_size: isFiniteNumber(record.sample_size) ? record.sample_size : 0,
  };
}

export function normalizeReplayMetrics(raw: unknown): ReplayMetrics {
  if (!raw || typeof raw !== "object") return {};
  const record = raw as Record<string, unknown>;
  const metrics: ReplayMetrics = {};

  const landing = metricBase(record.bundle_landing_rate);
  if (landing) metrics.bundle_landing_rate = landing;

  const tipEfficiency = metricBase(record.tip_efficiency);
  if (tipEfficiency) metrics.tip_efficiency = tipEfficiency;

  const latency = metricBase(record.slot_inclusion_latency);
  if (latency) {
    const source = record.slot_inclusion_latency as Record<string, unknown>;
    metrics.slot_inclusion_latency = {
      ...latency,
      mean: isFiniteNumber(source.mean) ? source.mean : undefined,
      median: isFiniteNumber(source.median) ? source.median : latency.value,
      p95: isFiniteNumber(source.p95) ? source.p95 : undefined,
      p99: isFiniteNumber(source.p99) ? source.p99 : undefined,
      samples: numberArray(source.samples),
    };
  }

  const breakEven = metricBase(record.cu_per_dollar_tip_breakeven);
  if (breakEven) {
    const source = record.cu_per_dollar_tip_breakeven as Record<string, unknown>;
    metrics.cu_per_dollar_tip_breakeven = {
      ...breakEven,
      tips: numberArray(source.tips),
      extracted_values: numberArray(source.extracted_values),
      ratios: numberArray(source.ratios),
    };
  }

  const skipRate = metricBase(record.skip_rate_cost);
  if (skipRate) metrics.skip_rate_cost = skipRate;

  const heatmap = metricBase(record.write_lock_heatmap);
  if (heatmap) {
    const source = record.write_lock_heatmap as Record<string, unknown>;
    const counts = Array.isArray(source.counts)
      ? source.counts.flatMap((item) => {
          if (!item || typeof item !== "object") return [];
          const count = item as Record<string, unknown>;
          if (
            typeof count.account !== "string" ||
            !isFiniteNumber(count.slot) ||
            !isFiniteNumber(count.count)
          ) {
            return [];
          }
          return [{ account: count.account, slot: count.slot, count: count.count }];
        })
      : [];
    metrics.write_lock_heatmap = {
      ...heatmap,
      accounts: stringArray(source.accounts),
      slots: numberArray(source.slots),
      counts,
      max_contention: isFiniteNumber(source.max_contention) ? source.max_contention : heatmap.value,
    };
  }

  const submission = metricBase(record.submission_path_comparison);
  if (submission) {
    const source = record.submission_path_comparison as Record<string, unknown>;
    metrics.submission_path_comparison = {
      ...submission,
      paths: stringArray(source.paths),
      submitted: numberArray(source.submitted),
      landed: numberArray(source.landed),
      landing_rates: numberArray(source.landing_rates),
      spread: isFiniteNumber(source.spread) ? source.spread : submission.value,
    };
  }

  return metrics;
}

export function hasReplayMetrics(metrics: ReplayMetrics): boolean {
  return REPLAY_METRIC_ORDER.some((key) => metrics[key] !== undefined);
}
