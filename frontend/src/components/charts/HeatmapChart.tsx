"use client";

interface HeatmapProps {
  data: number[][];
  rowLabels: number[];
  colLabels: number[];
  rowAxisLabel?: string;
  colAxisLabel?: string;
  valueLabel?: string;
  colorFn?: (value: number, min: number, max: number) => string;
  onCellClick?: (row: number, col: number) => void;
}

export default function HeatmapChart({
  data,
  rowLabels,
  colLabels,
  rowAxisLabel = "noise_freq →",
  colAxisLabel = "fee_rate →",
  valueLabel = "KL",
  colorFn,
  onCellClick,
}: HeatmapProps) {
  const allValues = data.flat();
  const minV = Math.min(...allValues);
  const maxV = Math.max(...allValues);

  const getColor = colorFn ?? ((_v: number, _min: number, _max: number) => {
    const intensity = 1 - (_v - _min) / (_max - _min);
    return `rgba(52, 211, 153, ${0.15 + intensity * 0.7})`;
  });

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontSize: ".82rem", color: "var(--text-2)" }}>{colAxisLabel}</span>
        <span style={{ fontSize: ".82rem", color: "var(--text-2)" }}>{valueLabel} (lower is better)</span>
      </div>
      <div style={{ display: "flex", gap: 2, alignItems: "end" }}>
        <span
          style={{
            fontSize: ".72rem",
            color: "var(--text-2)",
            writingMode: "vertical-rl",
            transform: "rotate(180deg)",
            paddingRight: 4,
          }}
        >
          {rowAxisLabel}
        </span>
        <div className="heatmap" style={{ gridTemplateColumns: `repeat(${colLabels.length},1fr)`, flex: 1 }}>
          {data.flatMap((row, r) =>
            row.map((v, c) => (
              <div
                key={`${r}-${c}`}
                className="heatmap-cell"
                style={{ background: getColor(v, minV, maxV) }}
                onClick={() => onCellClick?.(r, c)}
              >
                <span className="tooltip">
                  fee={colLabels[c]} nf={rowLabels[r]}
                  <br />
                  {valueLabel}={v.toFixed(3)}
                </span>
              </div>
            )),
          )}
        </div>
      </div>
    </div>
  );
}
