"use client";

import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { ReplayMetricResult } from "./types";

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function BundleLandingRateChart({
  metric,
  calibrationBand,
}: {
  metric?: ReplayMetricResult;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const value = metric?.value ?? 0;
  const pct = Math.max(0, Math.min(1, value));
  const droppedPct = 1 - pct;

  return (
    <ReplayChartFrame
      title="Bundle landing rate"
      value={metric ? percent(value) : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric ? (
        <div className="replay-stacked-rate" role="img" aria-label={`Bundle landing rate ${percent(value)}`}>
          <span className="landed" style={{ width: `${pct * 100}%` }} />
          <span className="missed" style={{ width: `${droppedPct * 100}%` }} />
          <div className="replay-rate-labels">
            <span>{percent(pct)} landed</span>
            <span>{percent(droppedPct)} missed</span>
          </div>
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
