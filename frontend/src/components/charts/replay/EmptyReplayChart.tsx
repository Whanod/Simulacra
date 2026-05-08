"use client";

export default function EmptyReplayChart({ label = "No metric samples" }: { label?: string }) {
  return (
    <div className="replay-empty-chart" role="img" aria-label={label}>
      <span>{label}</span>
    </div>
  );
}
