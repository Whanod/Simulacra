"use client";

import ReplayChartFrame from "./ReplayChartFrame";
import EmptyReplayChart from "./EmptyReplayChart";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { TipPnlSweepPoint } from "@/lib/services/replayService";

const SVG_SIZE = { width: 420, height: 240 };
const PAD = { top: 18, right: 20, bottom: 38, left: 66 };

function formatLamports(value: number): string {
  const abs = Math.abs(value);
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}k`;
  return `${sign}${abs.toFixed(0)}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function linePath(
  points: TipPnlSweepPoint[],
  xAt: (tip: number) => number,
  yAt: (pnl: number) => number,
): string {
  return points
    .map((point, index) => {
      const command = index === 0 ? "M" : "L";
      return `${command} ${xAt(point.tipLamports).toFixed(2)} ${yAt(
        point.pnlLamports,
      ).toFixed(2)}`;
    })
    .join(" ");
}

export default function TipPnlSweepChart({
  points,
  activeTipLamports,
  calibrationBand,
}: {
  points: TipPnlSweepPoint[];
  activeTipLamports?: number | null;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const cleanPoints = points
    .filter(
      (point) =>
        Number.isFinite(point.tipLamports) && Number.isFinite(point.pnlLamports),
    )
    .sort((a, b) => a.tipLamports - b.tipLamports);

  const maxTip = Math.max(1, ...cleanPoints.map((point) => point.tipLamports));
  const rawMinPnl = Math.min(0, ...cleanPoints.map((point) => point.pnlLamports));
  const rawMaxPnl = Math.max(0, ...cleanPoints.map((point) => point.pnlLamports));
  const pnlPadding = Math.max(1, (rawMaxPnl - rawMinPnl) * 0.1);
  const minPnl = rawMinPnl - pnlPadding;
  const maxPnl = rawMaxPnl + pnlPadding;
  const pnlRange = Math.max(1, maxPnl - minPnl);
  const chartW = SVG_SIZE.width - PAD.left - PAD.right;
  const chartH = SVG_SIZE.height - PAD.top - PAD.bottom;
  const xAt = (tip: number) => PAD.left + (clamp(tip, 0, maxTip) / maxTip) * chartW;
  const yAt = (pnl: number) =>
    PAD.top + (1 - (clamp(pnl, minPnl, maxPnl) - minPnl) / pnlRange) * chartH;
  const zeroY = yAt(0);
  const activeX =
    typeof activeTipLamports === "number" && Number.isFinite(activeTipLamports)
      ? xAt(activeTipLamports)
      : null;
  const currentPoint =
    activeTipLamports === null || activeTipLamports === undefined
      ? null
      : cleanPoints.find((point) => point.tipLamports === activeTipLamports) ??
        null;

  return (
    <ReplayChartFrame
      title="Tip vs. PnL"
      value={
        currentPoint
          ? `${formatLamports(currentPoint.pnlLamports)} pnl`
          : activeTipLamports !== null && activeTipLamports !== undefined
            ? `${formatLamports(activeTipLamports)} tip`
            : "—"
      }
      sampleSize={cleanPoints.length || undefined}
      empty={cleanPoints.length === 0}
      calibrationBand={calibrationBand}
    >
      {cleanPoints.length > 0 ? (
        <svg
          className="tip-pnl-sweep-chart"
          viewBox={`0 0 ${SVG_SIZE.width} ${SVG_SIZE.height}`}
          role="img"
          aria-label="Tip versus PnL sweep"
        >
          {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
            const x = PAD.left + chartW * tick;
            const tip = maxTip * tick;
            return (
              <g key={`x-${tick}`}>
                <line
                  className="tip-pnl-grid"
                  x1={x}
                  x2={x}
                  y1={PAD.top}
                  y2={PAD.top + chartH}
                />
                <text x={x} y={SVG_SIZE.height - 12} textAnchor="middle">
                  {formatLamports(tip)}
                </text>
              </g>
            );
          })}
          {[0, 0.5, 1].map((tick) => {
            const pnl = minPnl + pnlRange * tick;
            const y = yAt(pnl);
            return (
              <g key={`y-${tick}`}>
                <line
                  className="tip-pnl-grid"
                  x1={PAD.left}
                  x2={PAD.left + chartW}
                  y1={y}
                  y2={y}
                />
                <text x={PAD.left - 8} y={y + 4} textAnchor="end">
                  {formatLamports(pnl)}
                </text>
              </g>
            );
          })}
          <line
            className="tip-pnl-axis"
            x1={PAD.left}
            x2={PAD.left + chartW}
            y1={PAD.top + chartH}
            y2={PAD.top + chartH}
          />
          <line
            className="tip-pnl-axis"
            x1={PAD.left}
            x2={PAD.left}
            y1={PAD.top}
            y2={PAD.top + chartH}
          />
          <line
            className="tip-pnl-zero"
            x1={PAD.left}
            x2={PAD.left + chartW}
            y1={zeroY}
            y2={zeroY}
          />
          {activeX !== null ? (
            <line
              className="tip-pnl-active-tip"
              x1={activeX}
              x2={activeX}
              y1={PAD.top}
              y2={PAD.top + chartH}
            />
          ) : null}
          <path
            className="tip-pnl-line"
            d={linePath(cleanPoints, xAt, yAt)}
            fill="none"
          />
          {cleanPoints.map((point) => (
            <g key={`${point.tipLamports}-${point.pnlLamports}`}>
              <circle
                className={
                  point.pnlLamports >= 0 ? "tip-pnl-point profit" : "tip-pnl-point loss"
                }
                cx={xAt(point.tipLamports)}
                cy={yAt(point.pnlLamports)}
                r={4}
              >
                <title>{`tip ${formatLamports(point.tipLamports)} · pnl ${formatLamports(point.pnlLamports)}`}</title>
              </circle>
              {typeof point.mainnetPnlLamports === "number" ? (
                <circle
                  className="tip-pnl-mainnet-point"
                  cx={xAt(point.tipLamports)}
                  cy={yAt(point.mainnetPnlLamports)}
                  r={3.5}
                >
                  <title>{`mainnet pnl ${formatLamports(point.mainnetPnlLamports)}`}</title>
                </circle>
              ) : null}
            </g>
          ))}
          <text
            x={PAD.left + chartW / 2}
            y={SVG_SIZE.height - 4}
            textAnchor="middle"
            className="tip-pnl-axis-label"
          >
            tip lamports
          </text>
          <text
            x={16}
            y={PAD.top + chartH / 2}
            textAnchor="middle"
            className="tip-pnl-axis-label"
            transform={`rotate(-90 16 ${PAD.top + chartH / 2})`}
          >
            pnl lamports
          </text>
        </svg>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
