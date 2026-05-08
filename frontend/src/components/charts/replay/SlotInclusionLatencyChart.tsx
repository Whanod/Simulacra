"use client";

import BarChartCanvas from "../BarChartCanvas";
import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { SlotInclusionLatencyMetric } from "./types";

function buildLatencyBins(samples: number[]): { labels: string[]; counts: number[] } {
  const bins = new Map<string, number>([
    ["0", 0],
    ["1", 0],
    ["2-3", 0],
    ["4-7", 0],
    ["8+", 0],
  ]);
  for (const sample of samples) {
    const key = sample <= 0 ? "0" : sample === 1 ? "1" : sample <= 3 ? "2-3" : sample <= 7 ? "4-7" : "8+";
    bins.set(key, (bins.get(key) ?? 0) + 1);
  }
  return {
    labels: [...bins.keys()],
    counts: [...bins.values()],
  };
}

export default function SlotInclusionLatencyChart({
  metric,
  calibrationBand,
}: {
  metric?: SlotInclusionLatencyMetric;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const samples = metric?.samples ?? [];
  const bins = buildLatencyBins(samples);

  return (
    <ReplayChartFrame
      title="Slot inclusion latency"
      value={metric ? `${(metric.median ?? metric.value).toFixed(1)} slots` : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric && samples.length > 0 ? (
        <div>
          <BarChartCanvas
            data={bins.counts}
            labels={bins.labels}
            colors={["#6c8aff", "#22d3ee", "#34d399", "#fbbf24", "#f472b6"]}
            height={170}
          />
          <div className="replay-chart-footnote">
            p95 {(metric.p95 ?? 0).toFixed(1)} · p99 {(metric.p99 ?? 0).toFixed(1)}
          </div>
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
