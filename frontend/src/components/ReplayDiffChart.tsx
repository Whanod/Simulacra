"use client";

import CalibrationBand, {
  type CalibrationBandInput,
} from "@/components/CalibrationBand";

export interface ReplayDiffMetric {
  key: string;
  label: string;
  actual: number;
  counterfactual: number;
  unit?: string;
  fractionDigits?: number;
  calibrationBand?: CalibrationBandInput | null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function formatValue(value: number, fractionDigits = 0, unit?: string): string {
  const rendered = Number.isFinite(value)
    ? value.toLocaleString("en-US", {
        maximumFractionDigits: fractionDigits,
        minimumFractionDigits: fractionDigits,
      })
    : "0";
  if (unit === "%") return `${rendered}%`;
  return unit ? `${rendered} ${unit}` : rendered;
}

function formatDelta(value: number, fractionDigits = 0, unit?: string): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatValue(value, fractionDigits, unit)}`;
}

export default function ReplayDiffChart({
  metrics,
}: {
  metrics: ReplayDiffMetric[];
}) {
  if (metrics.length === 0) {
    return (
      <div className="bundle-empty-state" data-testid="replay-diff-empty">
        No replay comparison available.
      </div>
    );
  }

  return (
    <div className="replay-diff-chart" data-testid="replay-diff-chart">
      {metrics.map((metric) => {
        const max = Math.max(metric.actual, metric.counterfactual, 1);
        const actualX = 8 + clamp(metric.actual / max, 0, 1) * 84;
        const counterfactualX =
          8 + clamp(metric.counterfactual / max, 0, 1) * 84;
        const delta = metric.counterfactual - metric.actual;
        const digits = metric.fractionDigits ?? 0;

        return (
          <div
            className="replay-diff-metric"
            data-testid="replay-diff-metric"
            data-metric-key={metric.key}
            key={metric.key}
          >
            <div className="replay-diff-metric-heading">
              <span>{metric.label}</span>
              <strong>{formatDelta(delta, digits, metric.unit)}</strong>
            </div>
            <svg
              className="replay-diff-svg"
              role="img"
              aria-label={`${metric.label}: actual ${formatValue(
                metric.actual,
                digits,
                metric.unit,
              )}, counterfactual ${formatValue(
                metric.counterfactual,
                digits,
                metric.unit,
              )}`}
              viewBox="0 0 100 64"
              preserveAspectRatio="none"
            >
              <line className="replay-diff-track" x1="8" x2="92" y1="18" y2="18" />
              <line className="replay-diff-track" x1="8" x2="92" y1="46" y2="46" />
              <line
                className="replay-diff-connector"
                x1={actualX}
                x2={counterfactualX}
                y1="18"
                y2="46"
              />
              <circle className="replay-diff-actual-dot" cx={actualX} cy="18" r="3.5" />
              <circle
                className="replay-diff-counterfactual-dot"
                cx={counterfactualX}
                cy="46"
                r="3.5"
              />
            </svg>
            <div className="replay-diff-values">
              <span>Actual {formatValue(metric.actual, digits, metric.unit)}</span>
              <span>
                Counterfactual{" "}
                {formatValue(metric.counterfactual, digits, metric.unit)}
              </span>
            </div>
            <CalibrationBand
              input={metric.calibrationBand}
              metricLabel={metric.label}
            />
          </div>
        );
      })}
    </div>
  );
}
