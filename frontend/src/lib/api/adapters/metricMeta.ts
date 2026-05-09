/**
 * Display metadata for engine-emitted derived metrics.
 *
 * Keyed by the bare metric name (the part before any ``:variant`` suffix
 * the engine appends for per-agent variants like
 * ``lp_in_range_fraction:lp-tight``). Used by the results dashboard to
 * render one tile per derived metric without hard-coding which metrics
 * exist for which template.
 */

export type MetricDirection = "higher" | "lower" | "neutral";
export type MetricFormat = "percent" | "ratio" | "number" | "integer";

export interface MetricMeta {
  label: string;
  hint?: string;
  format: MetricFormat;
  digits?: number;
  direction: MetricDirection;
}

export const METRIC_META: Record<string, MetricMeta> = {
  lp_in_range_fraction: {
    label: "LP in-range fraction",
    hint: "Share of rounds the LP's range covered the spot tick",
    format: "percent",
    digits: 1,
    direction: "higher",
  },
  range_il: {
    label: "Range IL",
    hint: "Avg position value vs hodl, range-bounded",
    format: "percent",
    digits: 2,
    direction: "lower",
  },
  fees_vs_il_breakeven: {
    label: "Fees ÷ IL",
    hint: "Ratio > 1 means fees more than offset IL",
    format: "ratio",
    digits: 2,
    direction: "higher",
  },
  convergence_speed: {
    label: "Convergence speed",
    hint: "Round where rolling volatility stabilizes below threshold",
    format: "integer",
    direction: "lower",
  },
  lp_fees_per_liquidity: {
    label: "LP fees ÷ liquidity",
    hint: "LP fee revenue as a share of the position's deposited notional",
    format: "percent",
    digits: 4,
    direction: "higher",
  },
  total_volume_quote: {
    label: "Swap volume",
    hint: "Total swap volume in quote-token (USDC) human units",
    format: "number",
    digits: 0,
    direction: "neutral",
  },
  slippage: {
    label: "Slippage",
    hint: "1% test trade vs final pool",
    format: "percent",
    digits: 2,
    direction: "lower",
  },
  exitability: {
    label: "Exitability",
    hint: "Fraction of inventory liquidatable",
    format: "percent",
    digits: 1,
    direction: "higher",
  },
  manipulation_cost: {
    label: "Manipulation cost",
    hint: "Tip cost per price-unit moved",
    format: "number",
    digits: 0,
    direction: "higher",
  },
  kl_divergence: {
    label: "KL divergence",
    format: "ratio",
    digits: 4,
    direction: "neutral",
  },
};

export interface ParsedMetricKey {
  base: string;
  variant?: string;
  meta: MetricMeta;
  label: string;
}

export function parseMetricKey(key: string): ParsedMetricKey | null {
  const idx = key.indexOf(":");
  const base = idx === -1 ? key : key.slice(0, idx);
  const variant = idx === -1 ? undefined : key.slice(idx + 1);
  const meta = METRIC_META[base];
  if (!meta) return null;
  return {
    base,
    variant,
    meta,
    label: variant ? `${meta.label} · ${variant}` : meta.label,
  };
}

export function formatMetricValue(value: number, meta: MetricMeta): string {
  if (value === Number.POSITIVE_INFINITY) return "∞";
  if (value === Number.NEGATIVE_INFINITY) return "-∞";
  if (!Number.isFinite(value)) return "—";
  const digits = meta.digits ?? 2;
  switch (meta.format) {
    case "percent":
      return `${(value * 100).toFixed(digits)}%`;
    case "ratio":
      return value.toFixed(digits);
    case "integer":
      return Math.round(value).toLocaleString();
    case "number":
    default:
      return value.toLocaleString(undefined, { maximumFractionDigits: digits });
  }
}
