"use client";

import {
  calibrationBandView,
  type CalibrationBandInput,
} from "./calibrationBandView";

export { calibrationBandView };
export type { CalibrationBandInput, CalibrationBandView, CalibrationThreshold } from "./calibrationBandView";

interface CalibrationBandProps {
  input: CalibrationBandInput | null | undefined;
  /** Optional metric label to prefix in the overlay (e.g. "Pool price"). */
  metricLabel?: string;
}

export default function CalibrationBand({ input, metricLabel }: CalibrationBandProps) {
  const view = calibrationBandView(input);
  if (!view.visible) return null;
  const tone = view.breached
    ? "var(--red)"
    : view.withinThreshold
      ? "var(--green)"
      : "var(--text-2)";
  return (
    <div
      data-testid="calibration-band"
      data-within-threshold={view.withinThreshold ? "true" : "false"}
      data-breached={view.breached ? "true" : "false"}
      style={{
        marginTop: 8,
        fontSize: ".78rem",
        color: "var(--text-2)",
        fontFamily: "var(--font-mono)",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 8,
      }}
    >
      {metricLabel ? (
        <span style={{ color: "var(--text-2)" }}>{metricLabel}</span>
      ) : null}
      <span>{view.modelText}</span>
      <span>{view.mainnetText}</span>
      <span>{view.deltaText}</span>
      {view.relErrorText ? <span>{view.relErrorText}</span> : null}
      {view.thresholdText ? (
        <span style={{ color: tone }}>
          [{view.thresholdText}
          {view.marker ? ` ${view.marker}` : ""}]
        </span>
      ) : null}
    </div>
  );
}
