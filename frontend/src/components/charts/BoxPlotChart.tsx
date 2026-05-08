"use client";

import { useState, useCallback } from "react";

interface BoxPlotData {
  label: string;
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  color: string;
}

interface BoxPlotProps {
  data: BoxPlotData[];
  height?: number;
  onBoxClick?: (item: BoxPlotData, index: number) => void;
  showLegend?: boolean;
}

export type { BoxPlotData };

export default function BoxPlotChart({ data, height = 200, onBoxClick, showLegend = false }: BoxPlotProps) {
  const [hovered, setHovered] = useState<number | null>(null);

  const handleMouseEnter = useCallback((i: number) => setHovered(i), []);
  const handleMouseLeave = useCallback(() => setHovered(null), []);

  if (data.length === 0) return null;

  const allMin = Math.min(...data.map((d) => d.min));
  const allMax = Math.max(...data.map((d) => d.max));
  const range = allMax - allMin || 1;

  const scale = (v: number) => ((v - allMin) / range) * 100;

  return (
    <div className="chart-area" style={{ height, padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12, position: "relative" }}>
      {data.map((d, i) => (
        <div
          key={d.label}
          style={{ display: "flex", alignItems: "center", gap: 12, cursor: onBoxClick ? "pointer" : "default" }}
          onMouseEnter={() => handleMouseEnter(i)}
          onMouseLeave={handleMouseLeave}
          onClick={() => onBoxClick?.(d, i)}
        >
          <span style={{ fontSize: ".78rem", color: "var(--text-2)", minWidth: 60, textAlign: "right" }}>
            {d.label}
          </span>
          <div style={{ flex: 1, position: "relative", height: 20 }}>
            {/* whisker line */}
            <div
              style={{
                position: "absolute",
                left: `${scale(d.min)}%`,
                width: `${scale(d.max) - scale(d.min)}%`,
                top: 9,
                height: 2,
                background: d.color,
                opacity: hovered === i ? 0.7 : 0.4,
                transition: "opacity 0.15s",
              }}
            />
            {/* box */}
            <div
              style={{
                position: "absolute",
                left: `${scale(d.q1)}%`,
                width: `${scale(d.q3) - scale(d.q1)}%`,
                top: 2,
                height: 16,
                background: d.color,
                opacity: hovered === i ? 0.5 : 0.3,
                borderRadius: 3,
                transition: "opacity 0.15s",
              }}
            />
            {/* median */}
            <div
              style={{
                position: "absolute",
                left: `${scale(d.median)}%`,
                top: 0,
                width: 2,
                height: 20,
                background: d.color,
                opacity: hovered === i ? 1 : 0.8,
                transition: "opacity 0.15s",
              }}
            />
          </div>
          <span style={{ fontSize: ".72rem", fontFamily: "var(--font-mono)", color: "var(--text-1)", minWidth: 40 }}>
            {d.median.toFixed(2)}
          </span>
        </div>
      ))}

      {/* Tooltip */}
      {hovered !== null && (
        <div
          style={{
            position: "absolute",
            top: 4,
            right: 8,
            background: "var(--bg-0)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "8px 12px",
            fontSize: ".75rem",
            pointerEvents: "none",
            zIndex: 10,
            lineHeight: 1.6,
          }}
        >
          <div style={{ fontWeight: 600, color: data[hovered].color, marginBottom: 2 }}>
            {data[hovered].label}
          </div>
          <div style={{ fontFamily: "var(--font-mono)" }}>
            <span style={{ color: "var(--text-2)" }}>Min:</span> {data[hovered].min.toFixed(2)}
          </div>
          <div style={{ fontFamily: "var(--font-mono)" }}>
            <span style={{ color: "var(--text-2)" }}>Q1:</span> {data[hovered].q1.toFixed(2)}
          </div>
          <div style={{ fontFamily: "var(--font-mono)" }}>
            <span style={{ color: "var(--text-2)" }}>Median:</span> {data[hovered].median.toFixed(2)}
          </div>
          <div style={{ fontFamily: "var(--font-mono)" }}>
            <span style={{ color: "var(--text-2)" }}>Q3:</span> {data[hovered].q3.toFixed(2)}
          </div>
          <div style={{ fontFamily: "var(--font-mono)" }}>
            <span style={{ color: "var(--text-2)" }}>Max:</span> {data[hovered].max.toFixed(2)}
          </div>
        </div>
      )}

      {/* Legend */}
      {showLegend && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 4, paddingLeft: 72 }}>
          {data.map((d) => (
            <div key={d.label} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: ".72rem" }}>
              <div style={{ width: 8, height: 8, borderRadius: 2, background: d.color }} />
              <span style={{ color: "var(--text-2)" }}>{d.label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
