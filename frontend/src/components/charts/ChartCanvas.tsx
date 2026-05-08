"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface Dataset {
  data: number[];
  color: string;
  label?: string;
  width?: number;
  alpha?: number;
  fill?: boolean;
}

interface TooltipData {
  x: number;
  y: number;
  index: number;
  values: { label: string; value: number; color: string }[];
}

interface ChartCanvasProps {
  datasets: Dataset[];
  minY?: number;
  maxY?: number;
  decimals?: number;
  legend?: boolean;
  height?: number;
  onPointClick?: (index: number) => void;
  // When true, datasets are rendered as a stacked area: each band sits on top
  // of the ones before it in `datasets` order, and the fill spans between
  // its stacked top and the stacked top below it. Tooltips still read raw
  // `ds.data[idx]` so hover values reflect each dataset on its own, not the
  // running stack sum.
  stacked?: boolean;
}

const PAD = { top: 24, right: 16, bottom: 28, left: 54 };

export default function ChartCanvas({
  datasets,
  minY,
  maxY,
  decimals = 3,
  legend = true,
  height = 220,
  onPointClick,
  stacked = false,
}: ChartCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;
    const cW = W - PAD.left - PAD.right;
    const cH = H - PAD.top - PAD.bottom;

    const maxLen = Math.max(...datasets.map((d) => d.data.length));
    // For stacked mode, pre-compute each dataset's top-of-band y value
    // (running sum in `datasets` order). Bottom of the band for series i
    // is `stackedTops[i-1][r]` (or 0 for i=0). Tooltips keep reading raw
    // `ds.data[idx]` so hover shows per-series values, not the stack.
    const stackedTops: number[][] = stacked
      ? datasets.reduce<number[][]>((acc, ds, i) => {
          const below = i > 0 ? acc[i - 1] : null;
          const top = ds.data.map((v, r) => (below ? below[r] ?? 0 : 0) + v);
          acc.push(top);
          return acc;
        }, [])
      : [];
    const allVals: number[] = [];
    if (stacked) {
      stackedTops.forEach((row) => allVals.push(...row));
      // Guarantee the baseline is covered so a stack that sits well
      // above 0 still renders its lower band cleanly.
      allVals.push(0);
    } else {
      datasets.forEach((ds) => allVals.push(...ds.data));
    }
    let minV = minY ?? Math.min(...allVals);
    let maxV = maxY ?? Math.max(...allVals);
    if (minV === maxV) { minV -= 1; maxV += 1; }
    const rangeV = maxV - minV;

    // Background
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bg-2").trim();
    ctx.fillRect(0, 0, W, H);

    // Grid
    ctx.strokeStyle = "rgba(42,48,64,0.6)";
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = PAD.top + (cH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(PAD.left, y);
      ctx.lineTo(W - PAD.right, y);
      ctx.stroke();
      ctx.fillStyle = "#6b7a94";
      ctx.font = "10px -apple-system, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText((maxV - (rangeV / 4) * i).toFixed(decimals), PAD.left - 6, y + 3);
    }

    // X labels
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) {
      const x = PAD.left + (cW / 4) * i;
      ctx.fillText(String(Math.round((maxLen / 4) * i)), x, H - 8);
    }

    // Lines. In stacked mode we walk each series' stacked top, and the
    // fill spans down to the previous series' stacked top (or the chart
    // baseline for the first series) rather than all the way to y=0 —
    // otherwise overlapping fills wash out the bands.
    const xAt = (i: number) => PAD.left + (i / (maxLen - 1 || 1)) * cW;
    const yAt = (v: number) => PAD.top + (1 - (v - minV) / rangeV) * cH;
    datasets.forEach((ds, dsIndex) => {
      const topValues = stacked ? stackedTops[dsIndex] : ds.data;
      ctx.strokeStyle = ds.color;
      ctx.lineWidth = ds.width || 1.5;
      ctx.globalAlpha = ds.alpha || 1;
      ctx.beginPath();
      topValues.forEach((v, i) => {
        const x = xAt(i);
        const y = yAt(v);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.globalAlpha = 1;

      if (ds.fill) {
        ctx.globalAlpha = 0.08;
        ctx.fillStyle = ds.color;
        if (stacked && dsIndex > 0) {
          const below = stackedTops[dsIndex - 1];
          for (let i = topValues.length - 1; i >= 0; i--) {
            ctx.lineTo(xAt(i), yAt(below[i] ?? 0));
          }
          ctx.closePath();
          ctx.fill();
        } else {
          ctx.lineTo(PAD.left + cW, PAD.top + cH);
          ctx.lineTo(PAD.left, PAD.top + cH);
          ctx.closePath();
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
    });

    // Legend
    if (datasets.length > 1 && legend) {
      let lx = PAD.left + 8;
      datasets.forEach((ds) => {
        if (!ds.label) return;
        ctx.fillStyle = ds.color;
        ctx.fillRect(lx, 6, 12, 3);
        ctx.fillStyle = "#a8b2c4";
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(ds.label, lx + 16, 10);
        lx += ctx.measureText(ds.label).width + 32;
      });
    }
  }, [datasets, minY, maxY, decimals, legend, stacked]);

  useEffect(() => {
    draw();
    const handler = () => draw();
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, [draw]);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const container = containerRef.current;
      if (!container || datasets.length === 0) return;
      const rect = container.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const cW = rect.width - PAD.left - PAD.right;
      const maxLen = Math.max(...datasets.map((d) => d.data.length));
      const idx = Math.round(((mx - PAD.left) / cW) * (maxLen - 1));
      if (idx < 0 || idx >= maxLen) { setTooltip(null); return; }

      const values = datasets
        .filter((ds) => idx < ds.data.length)
        .map((ds) => ({
          label: ds.label || "",
          value: ds.data[idx],
          color: ds.color,
        }));

      setTooltip({ x: mx, y: e.clientY - rect.top, index: idx, values });
    },
    [datasets],
  );

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!onPointClick || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const cW = rect.width - PAD.left - PAD.right;
      const maxLen = Math.max(...datasets.map((d) => d.data.length));
      const idx = Math.round(((mx - PAD.left) / cW) * (maxLen - 1));
      if (idx >= 0 && idx < maxLen) onPointClick(idx);
    },
    [datasets, onPointClick],
  );

  return (
    <div
      ref={containerRef}
      className="chart-area"
      style={{ height, position: "relative" }}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setTooltip(null)}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} />
      {tooltip && (
        <div
          style={{
            position: "absolute",
            left: Math.min(tooltip.x + 12, (containerRef.current?.offsetWidth || 300) - 160),
            top: Math.max(tooltip.y - 50, 4),
            background: "var(--bg-0)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "8px 10px",
            fontSize: ".75rem",
            pointerEvents: "none",
            zIndex: 10,
            minWidth: 100,
          }}
        >
          <div style={{ color: "var(--text-2)", marginBottom: 4 }}>Round {tooltip.index}</div>
          {tooltip.values.map((v, i) => (
            <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: v.color, flexShrink: 0 }} />
              <span style={{ color: "var(--text-1)" }}>{v.label}:</span>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-0)" }}>
                {v.value.toFixed(4)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
