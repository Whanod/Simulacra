"use client";

interface TimelineEvent {
  round: number;
  type: string;
  color: string;
}

interface TimelineProps {
  events: TimelineEvent[];
  totalRounds: number;
  height?: number;
  onRoundClick?: (round: number) => void;
}

export default function TimelineChart({ events, totalRounds, height = 60, onRoundClick }: TimelineProps) {
  // Bucket events into bins
  const bins = 50;
  const binSize = Math.max(1, Math.ceil(totalRounds / bins));
  const densities: { count: number; types: Record<string, number> }[] = Array.from(
    { length: bins },
    () => ({ count: 0, types: {} }),
  );

  for (const ev of events) {
    const bin = Math.min(Math.floor(ev.round / binSize), bins - 1);
    densities[bin].count++;
    densities[bin].types[ev.type] = (densities[bin].types[ev.type] || 0) + 1;
  }

  const maxCount = Math.max(...densities.map((d) => d.count), 1);

  return (
    <div
      className="chart-area"
      style={{ height, display: "flex", alignItems: "end", gap: 1, padding: "8px 4px 4px" }}
    >
      {densities.map((d, i) => {
        const barH = (d.count / maxCount) * (height - 16);
        const dominantType = Object.entries(d.types).sort((a, b) => b[1] - a[1])[0];
        const color = dominantType
          ? events.find((e) => e.type === dominantType[0])?.color ?? "var(--accent)"
          : "var(--accent)";

        return (
          <div
            key={i}
            style={{
              flex: 1,
              height: Math.max(2, barH),
              background: color,
              opacity: 0.6,
              borderRadius: "2px 2px 0 0",
              cursor: onRoundClick ? "pointer" : undefined,
            }}
            title={`Rounds ${i * binSize}–${Math.min((i + 1) * binSize - 1, totalRounds)}: ${d.count} events`}
            onClick={() => onRoundClick?.(i * binSize)}
          />
        );
      })}
    </div>
  );
}
