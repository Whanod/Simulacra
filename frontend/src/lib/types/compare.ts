export interface CompareTarget {
  runId: string;
  label: string;
}

export interface MetricDelta {
  metric: string;
  valueA: number;
  valueB: number;
  delta: number;
  direction: "better" | "worse" | "neutral";
}

export interface CompareState {
  targets: [CompareTarget, CompareTarget] | null;
  specDiff: Record<string, { a: unknown; b: unknown }>;
  metricDeltas: MetricDelta[];
}
