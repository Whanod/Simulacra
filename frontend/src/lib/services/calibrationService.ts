import { apiFetch } from "@/lib/api/client";

export interface CalibrationThreshold {
  metric: string;
  thresholdRelative: number | null;
  thresholdAbsolute: number | null;
}

export interface CalibrationLastRun {
  runId: string | null;
  createdAt: string | null;
  status: string | null;
  mainnetAccuracyClaim: boolean | null;
  replayKind: string | null;
  perMetricError: Record<
    string,
    {
      absError: number;
      predicted: number | null;
      actual: number | null;
      supported: boolean;
    }
  >;
}

export type TrendDirection =
  | "improving"
  | "regressing"
  | "stable"
  | "no_history";

export interface CalibrationTrend {
  metric: string;
  direction: TrendDirection;
  delta: number | null;
  latestAbsError: number;
  previousAbsError?: number;
}

export interface CalibrationCorpusSlot {
  slot: number;
  programs: string[];
  expected: Record<string, unknown>;
  category: string | null;
  lastRun: CalibrationLastRun | null;
  trend: CalibrationTrend[];
  runCount: number;
}

export interface CalibrationCorpus {
  corpusRoot: string;
  thresholdsYaml: string;
  thresholds: CalibrationThreshold[];
  slots: CalibrationCorpusSlot[];
}

interface ApiCorpusBand {
  abs_error: number;
  predicted: number | null;
  actual: number | null;
  supported: boolean;
}

interface ApiLastRun {
  run_id: string | null;
  created_at: string | null;
  status: string | null;
  mainnet_accuracy_claim: boolean | null;
  replay_kind: string | null;
  per_metric_error: Record<string, ApiCorpusBand>;
}

interface ApiTrend {
  metric: string;
  direction: TrendDirection;
  delta: number | null;
  latest_abs_error: number;
  previous_abs_error?: number;
}

interface ApiSlot {
  slot: number;
  programs: string[];
  expected: Record<string, unknown>;
  category?: string | null;
  last_run: ApiLastRun | null;
  trend: ApiTrend[];
  run_count: number;
}

interface ApiCorpus {
  corpus_root: string;
  thresholds_yaml: string;
  thresholds: Array<{
    metric: string;
    threshold_relative: number | null;
    threshold_absolute: number | null;
  }>;
  slots: ApiSlot[];
}

function fromApiBand(b: ApiCorpusBand) {
  return {
    absError: b.abs_error,
    predicted: b.predicted,
    actual: b.actual,
    supported: b.supported,
  };
}

function fromApiLastRun(r: ApiLastRun | null): CalibrationLastRun | null {
  if (r === null) return null;
  const perMetricError: CalibrationLastRun["perMetricError"] = {};
  for (const [k, v] of Object.entries(r.per_metric_error ?? {})) {
    perMetricError[k] = fromApiBand(v);
  }
  return {
    runId: r.run_id,
    createdAt: r.created_at,
    status: r.status,
    mainnetAccuracyClaim: r.mainnet_accuracy_claim,
    replayKind: r.replay_kind,
    perMetricError,
  };
}

function fromApiTrend(t: ApiTrend): CalibrationTrend {
  return {
    metric: t.metric,
    direction: t.direction,
    delta: t.delta,
    latestAbsError: t.latest_abs_error,
    previousAbsError: t.previous_abs_error,
  };
}

function fromApiCorpus(raw: ApiCorpus): CalibrationCorpus {
  return {
    corpusRoot: raw.corpus_root,
    thresholdsYaml: raw.thresholds_yaml,
    thresholds: raw.thresholds.map((row) => ({
      metric: row.metric,
      thresholdRelative: row.threshold_relative,
      thresholdAbsolute: row.threshold_absolute,
    })),
    slots: raw.slots.map((s) => ({
      slot: s.slot,
      programs: s.programs ?? [],
      expected: s.expected ?? {},
      category: s.category ?? null,
      lastRun: fromApiLastRun(s.last_run),
      trend: (s.trend ?? []).map(fromApiTrend),
      runCount: s.run_count,
    })),
  };
}

export const calibrationService = {
  async getCorpus(): Promise<CalibrationCorpus> {
    const raw = await apiFetch<ApiCorpus>("/v1/calibration/corpus");
    return fromApiCorpus(raw);
  },
};
