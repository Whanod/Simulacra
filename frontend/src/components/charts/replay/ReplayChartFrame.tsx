"use client";

import type { ReactNode } from "react";
import CalibrationBand, {
  type CalibrationBandInput,
} from "@/components/CalibrationBand";

interface ReplayChartFrameProps {
  title: string;
  value: string;
  sampleSize?: number;
  children: ReactNode;
  empty?: boolean;
  calibrationBand?: CalibrationBandInput | null;
}

export default function ReplayChartFrame({
  title,
  value,
  sampleSize,
  children,
  empty = false,
  calibrationBand,
}: ReplayChartFrameProps) {
  return (
    <section
      className="replay-chart-block"
      data-testid="replay-chart-block"
      data-empty={empty ? "true" : "false"}
    >
      <div className="replay-chart-header">
        <div>
          <h4>{title}</h4>
          {typeof sampleSize === "number" ? (
            <span>{sampleSize.toLocaleString()} samples</span>
          ) : null}
        </div>
        <strong>{value}</strong>
      </div>
      {children}
      <CalibrationBand input={calibrationBand} metricLabel={title} />
    </section>
  );
}
