export interface SweepParam {
  parameter: string;
  min: number;
  max: number;
  steps: number;
}

export interface SweepConfig {
  id: string;
  params: SweepParam[];
  seeds: number[];
  parallelWorkers: number;
  targetMetric: string;
  direction: "lower" | "higher";
  validityGates: string[];
  totalRuns: number;
}

export interface SweepResult {
  rank: number;
  params: Record<string, number>;
  metrics: Record<string, number>;
  score: number;
}

export interface HeatmapCell {
  row: number;
  col: number;
  value: number;
  rowLabel: string;
  colLabel: string;
}

export interface SweepRun {
  id: string;
  name: string;
  status: "running" | "completed" | "queued";
  completedRuns: number;
  totalRuns: number;
  config: SweepConfig;
  results: SweepResult[];
  heatmap: number[][];
  heatmapRowLabels: number[];
  heatmapColLabels: number[];
  rows: Record<string, unknown>[];
  metricNames: string[];
  paramNames: string[];
}
