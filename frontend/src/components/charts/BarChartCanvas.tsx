"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface BarChartProps {
  data: number[];
  colors: string[];
  labels?: string[];
  height?: number;
  onBarClick?: (value: number, index: number) => void;
  showLegend?: boolean;
}

const PAD = { top: 20, right: 16, bottom: 32, left: 50 };

export default function BarChartCanvas({ data, colors, labels, height = 220, onBarClick, showLegend = false }: BarChartProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredBar, setHoveredBar] = useState<number | null>(null);

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

    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bg-2").trim();
    ctx.fillRect(0, 0, W, H);

    if (data.length === 0) return;
    const maxV = Math.max(...data.map(Math.abs), 1);
    const barW = (cW / data.length) * 0.7;
    const gap = (cW / data.length) * 0.3;

    data.forEach((v, i) => {
      const x = PAD.left + i * (barW + gap) + gap / 2;
      const barH = (Math.abs(v) / maxV) * cH;
      ctx.fillStyle = colors[i % colors.length];
      ctx.globalAlpha = i === hoveredBar ? 0.95 : 0.7;
      ctx.beginPath();
      ctx.roundRect(x, v >= 0 ? PAD.top + cH - barH : PAD.top + cH, barW, barH, [3, 3, 0, 0]);
      ctx.fill();
      ctx.globalAlpha = 1;
      if (labels) {
        ctx.fillStyle = "#6b7a94";
        ctx.font = "9px -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(labels[i], x + barW / 2, H - 10);
      }
    });
  }, [data, colors, labels, hoveredBar]);

  useEffect(() => {
    draw();
    const handler = () => draw();
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, [draw]);

  const getBarIndex = useCallback(
    (clientX: number): number | null => {
      const container = containerRef.current;
      if (!container || data.length === 0) return null;
      const rect = container.getBoundingClientRect();
      const mx = clientX - rect.left;
      const cW = rect.width - PAD.left - PAD.right;
      const barW = (cW / data.length) * 0.7;
      const gap = (cW / data.length) * 0.3;

      for (let i = 0; i < data.length; i++) {
        const x = PAD.left + i * (barW + gap) + gap / 2;
        if (mx >= x && mx <= x + barW) return i;
      }
      return null;
    },
    [data],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => setHoveredBar(getBarIndex(e.clientX)),
    [getBarIndex],
  );

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!onBarClick) return;
      const idx = getBarIndex(e.clientX);
      if (idx !== null) onBarClick(data[idx], idx);
    },
    [onBarClick, getBarIndex, data],
  );

  return (
    <div
      ref={containerRef}
      className="chart-area"
      style={{ height, position: "relative" }}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setHoveredBar(null)}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} style={{ cursor: onBarClick ? "pointer" : "default" }} />
      {hoveredBar !== null && (
        <div
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            background: "var(--bg-0)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "6px 10px",
            fontSize: ".75rem",
            pointerEvents: "none",
          }}
        >
          {labels && (
            <div style={{ color: "var(--text-2)", marginBottom: 2, fontSize: ".7rem" }}>
              {labels[hoveredBar]}
            </div>
          )}
          <span style={{ fontFamily: "var(--font-mono)", color: data[hoveredBar] >= 0 ? "var(--green)" : "var(--red)" }}>
            {data[hoveredBar] >= 0 ? "+" : ""}
            {data[hoveredBar].toFixed(0)}
          </span>
        </div>
      )}
      {showLegend && labels && (
        <div
          style={{
            position: "absolute",
            bottom: 2,
            left: PAD.left,
            right: PAD.right,
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            justifyContent: "center",
          }}
        >
          {labels.map((l, i) => (
            <div key={l} style={{ display: "flex", alignItems: "center", gap: 3, fontSize: ".65rem" }}>
              <div style={{ width: 6, height: 6, borderRadius: 1, background: colors[i % colors.length], opacity: 0.8 }} />
              <span style={{ color: "var(--text-2)" }}>{l}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
