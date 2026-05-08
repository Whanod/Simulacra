"use client";

import BarChartCanvas from "../BarChartCanvas";
import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { SubmissionPathComparisonMetric } from "./types";

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function SubmissionPathComparisonChart({
  metric,
  calibrationBand,
}: {
  metric?: SubmissionPathComparisonMetric;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const paths = metric?.paths ?? [];
  const rates = metric?.landing_rates ?? [];

  return (
    <ReplayChartFrame
      title="Submission path comparison"
      value={metric ? `${percent(metric.spread ?? metric.value)} spread` : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric && paths.length > 0 ? (
        <div>
          <BarChartCanvas
            data={rates.map((rate) => rate * 100)}
            labels={paths.map((path) => path.replaceAll("_", " "))}
            colors={["#34d399", "#6c8aff", "#fbbf24", "#f472b6"]}
            height={170}
          />
          <div className="replay-chart-footnote">
            {paths.map((path, index) => `${path}: ${percent(rates[index] ?? 0)}`).join(" · ")}
          </div>
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
