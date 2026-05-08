"use client";

import { type ReactNode } from "react";
import StatCard from "@/components/ui/StatCard";
import {
  formatMetricValue,
  parseMetricKey,
  type ParsedMetricKey,
} from "@/lib/api/adapters/metricMeta";

interface Props {
  metrics: Record<string, number>;
  emptyHint?: string;
  // Extra StatCards rendered inside the same ``grid-4`` after the dynamic
  // engine-derived tiles. Used by the results page to append client-derived
  // essentials (LP fee yield, tick crossings, drawdown, vol) so the grid
  // fills its rows continuously instead of leaving a gap before a second
  // grid begins on a fresh row.
  trailing?: ReactNode;
}

interface Tile {
  key: string;
  value: number;
  parsed: ParsedMetricKey;
}

/**
 * One tile per engine-emitted derived metric. Drives off the actual
 * payload (``metadata.derived_metrics``) so each template surfaces what
 * its engine path computed — no hard-coded tile list.
 *
 * Per-agent variants (``range_il:lp-tight``) sort directly under their
 * pool-wide aggregate so contrasts read left-to-right.
 */
export default function RecommendedMetricsGrid({ metrics, emptyHint, trailing }: Props) {
  const entries: Tile[] = [];
  for (const [key, value] of Object.entries(metrics)) {
    const parsed = parseMetricKey(key);
    if (parsed === null) continue;
    entries.push({ key, value, parsed });
  }

  if (entries.length === 0 && !trailing) {
    if (!emptyHint) return null;
    return (
      <div
        style={{
          marginBottom: 20,
          padding: "12px 16px",
          color: "var(--text-2)",
          fontSize: ".82rem",
          border: "1px dashed var(--border)",
          borderRadius: 6,
        }}
      >
        {emptyHint}
      </div>
    );
  }

  entries.sort((a, b) => {
    if (a.parsed.base !== b.parsed.base) {
      return a.parsed.base.localeCompare(b.parsed.base);
    }
    if (!a.parsed.variant && b.parsed.variant) return -1;
    if (a.parsed.variant && !b.parsed.variant) return 1;
    return (a.parsed.variant ?? "").localeCompare(b.parsed.variant ?? "");
  });

  return (
    <div className="grid-4" style={{ marginBottom: 20 }}>
      {entries.map(({ key, value, parsed }) => (
        <StatCard
          key={key}
          label={parsed.label}
          value={formatMetricValue(value, parsed.meta)}
          hint={parsed.meta.hint}
        />
      ))}
      {trailing}
    </div>
  );
}
