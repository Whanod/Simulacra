"use client";

import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { ReplayMetricResult } from "./types";

function lamports(value: number): string {
  return `${Math.round(value).toLocaleString()} lamports`;
}

export default function SkipRateCostChart({
  metric,
  calibrationBand,
}: {
  metric?: ReplayMetricResult;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const value = metric?.value ?? 0;
  const intensity = Math.max(0, Math.min(1, value / 1_000_000));

  return (
    <ReplayChartFrame
      title="Skip-rate cost"
      value={metric ? lamports(value) : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric ? (
        <div className="replay-skip-cost" role="img" aria-label={`Skip-rate cost ${lamports(value)}`}>
          <span style={{ height: `${Math.max(6, intensity * 100)}%` }} />
          <div>
            <strong>{lamports(value)}</strong>
            <p>lost EV from skipped slots</p>
          </div>
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
