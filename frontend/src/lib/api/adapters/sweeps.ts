import type {
  SweepConfig,
  SweepParam,
  SweepResult,
  SweepRun,
} from "@/lib/types/sweeps";

// ── Backend shapes ─────────────────────────────────────────────────────────

export interface ApiSweepSummary {
  row_count?: number;
  metric_names?: string[];
  param_names?: string[];
  seeds?: number[];
  [key: string]: unknown;
}

export interface ApiSweepSpec {
  spec?: Record<string, unknown>;
  param_grid?: Record<string, number[]>;
  num_runs?: number;
  seeds?: number[];
  master_seed?: number | null;
  metrics?: Record<string, { type?: string; path?: string }>;
  [key: string]: unknown;
}

export interface ApiSweep {
  sweep_id: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  summary?: ApiSweepSummary;
  spec?: ApiSweepSpec;
  [key: string]: unknown;
}

export interface ApiSweepsListResponse {
  sweeps: ApiSweep[];
  count?: number;
  limit?: number;
  offset?: number;
}

export type ApiSweepRow = Record<string, unknown>;

export interface ApiSweepRowsResponse {
  sweep_id: string;
  data: ApiSweepRow[];
}

export interface ApiSweepRecommendations {
  top_configurations?: Array<Record<string, unknown>>;
  rejected_configurations?: Array<Record<string, unknown>>;
  next_experiment?: Record<string, unknown>;
}

// ── Status mapping ─────────────────────────────────────────────────────────

function mapSweepStatus(raw: string | undefined): SweepRun["status"] {
  if (!raw) return "queued";
  const s = raw.toLowerCase();
  if (s === "completed") return "completed";
  if (s === "running" || s === "live") return "running";
  return "queued";
}

// ── Helpers ────────────────────────────────────────────────────────────────

function uniqueSorted(values: number[]): number[] {
  return Array.from(new Set(values)).sort((a, b) => a - b);
}

function variance(values: number[]): number {
  if (values.length < 2) return 0;
  const mean = values.reduce((s, v) => s + v, 0) / values.length;
  return values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length;
}

function numericRowValue(row: ApiSweepRow, key: string): number | undefined {
  const v = row[key];
  return typeof v === "number" ? v : undefined;
}

function pickHeatmapAxes(
  paramNames: string[],
  rows: ApiSweepRow[],
): [string | null, string | null] {
  if (paramNames.length === 0) return [null, null];
  if (paramNames.length === 1) return [paramNames[0], null];
  if (paramNames.length === 2) return [paramNames[0], paramNames[1]];
  const scored = paramNames
    .map((name) => ({
      name,
      variance: variance(
        rows.map((r) => numericRowValue(r, name)).filter((v): v is number => v !== undefined),
      ),
    }))
    .sort((a, b) => b.variance - a.variance);
  return [scored[0]?.name ?? null, scored[1]?.name ?? null];
}

function computeHeatmap(
  rows: ApiSweepRow[],
  rowParam: string | null,
  colParam: string | null,
  targetMetric: string,
): {
  heatmap: number[][];
  heatmapRowLabels: number[];
  heatmapColLabels: number[];
} {
  if (!rowParam) {
    return { heatmap: [], heatmapRowLabels: [], heatmapColLabels: [] };
  }
  const rowVals = uniqueSorted(
    rows.map((r) => numericRowValue(r, rowParam)).filter((v): v is number => v !== undefined),
  );
  if (!colParam) {
    const matrix: number[][] = [];
    for (const r of rowVals) {
      const matching = rows.filter((row) => numericRowValue(row, rowParam) === r);
      const metricVals = matching
        .map((row) => numericRowValue(row, targetMetric))
        .filter((v): v is number => v !== undefined);
      const mean = metricVals.length
        ? metricVals.reduce((s, v) => s + v, 0) / metricVals.length
        : 0;
      matrix.push([mean]);
    }
    return { heatmap: matrix, heatmapRowLabels: rowVals, heatmapColLabels: [0] };
  }
  const colVals = uniqueSorted(
    rows.map((r) => numericRowValue(r, colParam)).filter((v): v is number => v !== undefined),
  );
  const matrix: number[][] = [];
  for (const r of rowVals) {
    const row: number[] = [];
    for (const c of colVals) {
      const matching = rows.filter(
        (sweepRow) =>
          numericRowValue(sweepRow, rowParam) === r &&
          numericRowValue(sweepRow, colParam) === c,
      );
      const metricVals = matching
        .map((sweepRow) => numericRowValue(sweepRow, targetMetric))
        .filter((v): v is number => v !== undefined);
      const mean = metricVals.length
        ? metricVals.reduce((s, v) => s + v, 0) / metricVals.length
        : 0;
      row.push(mean);
    }
    matrix.push(row);
  }
  return { heatmap: matrix, heatmapRowLabels: rowVals, heatmapColLabels: colVals };
}

function rowsToSweepResults(
  rows: ApiSweepRow[],
  paramNames: string[],
  metricNames: string[],
  targetMetric: string,
  direction: "lower" | "higher",
  topK = 10,
): SweepResult[] {
  const scored = rows.map((row) => {
    const params: Record<string, number> = {};
    for (const name of paramNames) {
      const v = numericRowValue(row, name);
      if (v !== undefined) params[name] = v;
    }
    const metrics: Record<string, number> = {};
    for (const name of metricNames) {
      const v = numericRowValue(row, name);
      if (v !== undefined) metrics[name] = v;
    }
    const score = metrics[targetMetric] ?? 0;
    return { params, metrics, score };
  });
  scored.sort((a, b) => (direction === "lower" ? a.score - b.score : b.score - a.score));
  return scored.slice(0, topK).map((row, i) => ({
    rank: i + 1,
    params: row.params,
    metrics: row.metrics,
    score: row.score,
  }));
}

// ── Config mapping ─────────────────────────────────────────────────────────

export function configFromSpec(raw: ApiSweep, rows: ApiSweepRow[]): SweepConfig {
  const spec = raw.spec || {};
  const paramGrid = spec.param_grid || {};
  const metricNames = Object.keys(spec.metrics || {});
  const params: SweepParam[] = Object.entries(paramGrid).map(([parameter, values]) => {
    const arr = Array.isArray(values) ? values : [];
    const min = arr.length ? Math.min(...arr) : 0;
    const max = arr.length ? Math.max(...arr) : 0;
    return { parameter, min, max, steps: arr.length };
  });
  const seeds = Array.isArray(spec.seeds) ? spec.seeds.slice() : [];
  return {
    id: raw.sweep_id,
    params,
    seeds,
    parallelWorkers: 1,
    targetMetric: metricNames[0] ?? "composite_score",
    direction: "higher",
    validityGates: [],
    totalRuns: rows.length || raw.summary?.row_count || 0,
  };
}

// ── Top-level SweepRun construction ────────────────────────────────────────

function countCompleted(rows: ApiSweepRow[]): number {
  return rows.filter((row) => {
    const status = row.status;
    return typeof status === "string" ? status.toLowerCase() === "completed" : false;
  }).length;
}

function synthName(raw: ApiSweep): string {
  const params = raw.summary?.param_names ?? [];
  if (params.length > 0) return params.join(" × ");
  return `sweep-${raw.sweep_id.slice(0, 6)}`;
}

export function fromApiSweep(raw: ApiSweep, rows: ApiSweepRow[] = []): SweepRun {
  const summary = raw.summary || {};
  const paramNames = summary.param_names ?? Object.keys(raw.spec?.param_grid ?? {});
  const metricNames = summary.metric_names ?? Object.keys(raw.spec?.metrics ?? {});
  const config = configFromSpec(raw, rows);
  const target = config.targetMetric;

  const results = rowsToSweepResults(rows, paramNames, metricNames, target, config.direction, 10);
  const [rowParam, colParam] = pickHeatmapAxes(paramNames, rows);
  const heatmapData = computeHeatmap(rows, rowParam, colParam, target);

  return {
    id: raw.sweep_id,
    name: synthName(raw),
    status: mapSweepStatus(raw.status),
    completedRuns: countCompleted(rows),
    totalRuns: rows.length || summary.row_count || 0,
    config,
    results,
    heatmap: heatmapData.heatmap,
    heatmapRowLabels: heatmapData.heatmapRowLabels,
    heatmapColLabels: heatmapData.heatmapColLabels,
    rows,
    metricNames,
    paramNames,
  };
}

export function fromApiSweeps(raws: ApiSweep[]): SweepRun[] {
  return raws.map((r) => fromApiSweep(r, []));
}

// ── Reverse: SweepConfig → backend SweepRunRequest ─────────────────────────

function linspace(min: number, max: number, steps: number): number[] {
  if (steps <= 0) return [];
  if (steps === 1) return [min];
  const out: number[] = [];
  for (let i = 0; i < steps; i++) {
    out.push(min + ((max - min) * i) / (steps - 1));
  }
  return out;
}

export interface SweepRunRequest {
  spec: Record<string, unknown>;
  param_grid: Record<string, number[]>;
  seeds?: number[];
  master_seed?: number | null;
  num_runs?: number;
  metrics: Record<string, { type: string; path: string }>;
}

export function sweepConfigToApi(
  spec: Record<string, unknown>,
  config: SweepConfig,
): SweepRunRequest {
  const paramGrid: Record<string, number[]> = {};
  for (const p of config.params) {
    paramGrid[p.parameter] = linspace(p.min, p.max, p.steps);
  }
  const metricName = config.targetMetric || "rounds";
  return {
    spec,
    param_grid: paramGrid,
    seeds: config.seeds && config.seeds.length > 0 ? config.seeds : undefined,
    metrics: {
      [metricName]: { type: "field", path: "num_rounds_executed" },
    },
  };
}

// ── Recommendations → SweepResult[] ────────────────────────────────────────

export function recommendationsToResults(
  raw: ApiSweepRecommendations,
  paramNames: string[],
  metricNames: string[],
): SweepResult[] {
  const top = raw.top_configurations ?? [];
  return top.map((entry, i) => {
    const params: Record<string, number> = {};
    for (const name of paramNames) {
      const v = entry[name];
      if (typeof v === "number") params[name] = v;
    }
    const metrics: Record<string, number> = {};
    for (const name of metricNames) {
      const v = entry[name];
      if (typeof v === "number") metrics[name] = v;
    }
    const compositeScore = entry["composite_score"];
    return {
      rank: i + 1,
      params,
      metrics,
      score: typeof compositeScore === "number" ? compositeScore : 0,
    };
  });
}
