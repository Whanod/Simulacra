"use client";

import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { ReplayMetricResult } from "./types";

function ratioLabel(value: number): string {
  return `${value.toFixed(2)}x`;
}

export default function TipEfficiencyChart({
  metric,
  calibrationBand,
}: {
  metric?: ReplayMetricResult;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const value = metric?.value ?? 0;
  const capped = Math.max(0, Math.min(1.5, value));
  const width = (capped / 1.5) * 100;

  return (
    <ReplayChartFrame
      title="Tip efficiency"
      value={metric ? ratioLabel(value) : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric ? (
        <div className="replay-efficiency-chart" role="img" aria-label={`Tip efficiency ${ratioLabel(value)}`}>
          <div className="replay-axis">
            <span>0</span>
            <span>break-even 1.0</span>
            <span>1.5+</span>
          </div>
          <div className="replay-efficiency-track">
            <span className="break-even" />
            <span className="value" style={{ width: `${width}%` }} />
          </div>
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
