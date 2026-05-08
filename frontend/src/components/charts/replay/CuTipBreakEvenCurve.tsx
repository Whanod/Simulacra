"use client";

import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { CuTipBreakEvenMetric } from "./types";

const SVG_SIZE = { width: 360, height: 220 };
const PAD = { top: 18, right: 18, bottom: 34, left: 54 };

function fmtLamports(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toFixed(0);
}

export default function CuTipBreakEvenCurve({
  metric,
  calibrationBand,
}: {
  metric?: CuTipBreakEvenMetric;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const tips = metric?.tips ?? [];
  const evs = metric?.extracted_values ?? [];
  const count = Math.min(tips.length, evs.length);
  const maxValue = Math.max(1, ...tips.slice(0, count), ...evs.slice(0, count));
  const chartW = SVG_SIZE.width - PAD.left - PAD.right;
  const chartH = SVG_SIZE.height - PAD.top - PAD.bottom;
  const xAt = (tip: number) => PAD.left + (tip / maxValue) * chartW;
  const yAt = (ev: number) => PAD.top + (1 - ev / maxValue) * chartH;

  return (
    <ReplayChartFrame
      title="CU/$ tip break-even"
      value={metric ? `${((metric.value ?? 0) * 100).toFixed(1)}% clear` : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric && count > 0 ? (
        <svg
          className="replay-scatter"
          viewBox={`0 0 ${SVG_SIZE.width} ${SVG_SIZE.height}`}
          role="img"
          aria-label="Tip paid versus extracted value scatter"
        >
          <line x1={PAD.left} y1={PAD.top + chartH} x2={PAD.left + chartW} y2={PAD.top + chartH} className="axis" />
          <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + chartH} className="axis" />
          {[0.25, 0.5, 0.75, 1].map((tick) => (
            <g key={tick}>
              <line
                x1={PAD.left}
                y1={PAD.top + chartH * (1 - tick)}
                x2={PAD.left + chartW}
                y2={PAD.top + chartH * (1 - tick)}
                className="grid"
              />
              <text x={PAD.left - 8} y={PAD.top + chartH * (1 - tick) + 4} textAnchor="end">
                {fmtLamports(maxValue * tick)}
              </text>
              <text x={PAD.left + chartW * tick} y={PAD.top + chartH + 20} textAnchor="middle">
                {fmtLamports(maxValue * tick)}
              </text>
            </g>
          ))}
          <line
            x1={PAD.left}
            y1={PAD.top + chartH}
            x2={PAD.left + chartW}
            y2={PAD.top}
            className="break-even"
          />
          {tips.slice(0, count).map((tip, index) => {
            const ev = evs[index];
            const cleared = ev >= tip;
            return (
              <circle
                key={`${tip}-${ev}-${index}`}
                cx={xAt(tip)}
                cy={yAt(ev)}
                r={4}
                className={cleared ? "cleared" : "shortfall"}
              >
                <title>{`tip ${fmtLamports(tip)} · EV ${fmtLamports(ev)}`}</title>
              </circle>
            );
          })}
          <text x={PAD.left + chartW / 2} y={SVG_SIZE.height - 5} textAnchor="middle" className="axis-label">
            tip paid
          </text>
          <text x={14} y={PAD.top + chartH / 2} textAnchor="middle" className="axis-label" transform={`rotate(-90 14 ${PAD.top + chartH / 2})`}>
            extracted value
          </text>
        </svg>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
