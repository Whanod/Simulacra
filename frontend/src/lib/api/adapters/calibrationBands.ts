// PRD US-004 line 781: extract per-metric calibration bands from a replay
// run's `result.replay_diff` payload, plus expose the per-metric thresholds
// configured in `solana-plans/calibration/thresholds.yaml`. The studio
// overlay (`<CalibrationBand>`) consumes the result.
//
// Replay artifacts persist `result.replay_diff.per_metric_error` as
// `dict[str, ErrorBand]` keyed by exact metric name. Replay chart callers pass
// exact chart keys through `byMetric`; aggregate legacy charts can still use
// the family map for prefixes such as `pool_price:<pool>`.

import type { ApiRunResult } from "./runs";
import type { CalibrationThreshold } from "@/components/calibrationBandView";

export interface RawErrorBand {
  metric: string;
  predicted: number;
  actual: number | null;
  abs_error?: number | null;
  rel_error?: number | null;
  supported?: boolean;
}

export interface CalibrationBands {
  /** Keyed by full metric id (e.g. `pool_price`, `pool_price:SOL/USDC`). */
  byMetric: Record<string, RawErrorBand>;
  /** First band whose metric matches the family prefix (e.g. `pool_price`). */
  family: Record<string, RawErrorBand | undefined>;
}

export const EMPTY_CALIBRATION_BANDS: CalibrationBands = {
  byMetric: {},
  family: {},
};

const METRIC_FAMILIES = [
  "bundle_landing_rate",
  "tip_efficiency",
  "slot_inclusion_latency",
  "cu_per_dollar_tip_breakeven",
  "skip_rate_cost",
  "write_lock_heatmap",
  "submission_path_comparison",
  "pool_price",
  "lp_balance",
  "total_volume",
  "liquidations_triggered",
  "tips_paid",
];

/**
 * Per-metric thresholds mirrored from `solana-plans/calibration/thresholds.yaml`.
 * Kept in sync by hand until the backend ships them in `replay_diff`.
 * `pool_price:*` and `lp_balance:*` (per-pool / per-agent metrics) inherit
 * the family threshold via prefix match.
 */
export const CALIBRATION_THRESHOLDS: Record<string, CalibrationThreshold> = {
  bundle_landing_rate: { absolute: 0.05 },
  tip_efficiency: { relative: 0.1 },
  slot_inclusion_latency: { absolute: 1 },
  cu_per_dollar_tip_breakeven: { absolute: 0.05 },
  skip_rate_cost: { relative: 0.1 },
  write_lock_heatmap: { absolute: 1 },
  submission_path_comparison: { absolute: 0.05 },
  pool_price: { relative: 0.005 },
  lp_balance: { relative: 0.005 },
  total_volume: { relative: 0.02 },
  liquidations_triggered: { absolute: 1 },
  tips_paid: { relative: 0.1 },
};

export function thresholdForMetric(
  metricKey: string,
): CalibrationThreshold | null {
  if (CALIBRATION_THRESHOLDS[metricKey]) return CALIBRATION_THRESHOLDS[metricKey];
  for (const family of METRIC_FAMILIES) {
    if (metricKey === family || metricKey.startsWith(`${family}:`)) {
      return CALIBRATION_THRESHOLDS[family] ?? null;
    }
  }
  return null;
}

function familyOf(metricKey: string): string | null {
  for (const family of METRIC_FAMILIES) {
    if (metricKey === family || metricKey.startsWith(`${family}:`)) {
      return family;
    }
  }
  return null;
}

function isErrorBandLike(value: unknown): value is RawErrorBand {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.metric === "string" &&
    typeof v.predicted === "number" &&
    (typeof v.actual === "number" || v.actual === null || v.actual === undefined)
  );
}

/**
 * Pull the per-metric ErrorBand map out of `result.replay_diff`. Tolerates
 * three shapes the backend might land:
 *   1. `{per_metric_error: {<metric>: ErrorBand}}` — preferred.
 *   2. `{<metric>: ErrorBand}` — flat dict.
 *   3. anything else / null — returns empty bands.
 */
export function extractCalibrationBands(
  result: ApiRunResult | null | undefined,
): CalibrationBands {
  if (!result) return EMPTY_CALIBRATION_BANDS;
  const diff = (result as { replay_diff?: unknown }).replay_diff;
  if (!diff || typeof diff !== "object") return EMPTY_CALIBRATION_BANDS;

  const candidate =
    (diff as { per_metric_error?: unknown }).per_metric_error ?? diff;
  if (!candidate || typeof candidate !== "object") return EMPTY_CALIBRATION_BANDS;

  const byMetric: Record<string, RawErrorBand> = {};
  const family: Record<string, RawErrorBand | undefined> = {};
  for (const [key, raw] of Object.entries(candidate as Record<string, unknown>)) {
    if (!isErrorBandLike(raw)) continue;
    const band: RawErrorBand = {
      metric: raw.metric,
      predicted: raw.predicted,
      actual: raw.actual ?? null,
      abs_error:
        raw.abs_error ??
        (raw as { absolute_error?: number | null }).absolute_error ??
        null,
      rel_error:
        raw.rel_error ??
        (raw as { relative_error?: number | null }).relative_error ??
        null,
      supported:
        typeof raw.supported === "boolean"
          ? raw.supported
          : raw.actual !== null && raw.actual !== undefined,
    };
    byMetric[key] = band;
    const fam = familyOf(key);
    if (fam && family[fam] === undefined) family[fam] = band;
  }
  return { byMetric, family };
}
