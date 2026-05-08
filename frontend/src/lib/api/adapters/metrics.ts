import type { SimMetrics } from "@/lib/types/simulations";

export interface ApiMetricsResponse {
  metrics?: Record<string, number>;
  [key: string]: unknown;
}

const FIELD_MAP: Record<string, keyof SimMetrics> = {
  kl_divergence: "klDivergence",
  klDivergence: "klDivergence",
  convergence_speed: "convergenceSpeed",
  convergenceSpeed: "convergenceSpeed",
  lp_profitability: "lpProfitability",
  lpProfitability: "lpProfitability",
  manipulation_cost: "manipulationCost",
  manipulationCost: "manipulationCost",
  max_drawdown: "maxDrawdown",
  maxDrawdown: "maxDrawdown",
  rolling_vol: "rollingVol",
  rollingVol: "rollingVol",
  twap: "twap",
  slippage: "slippage",
  exitability: "exitability",
  composite_score: "compositeScore",
  compositeScore: "compositeScore",
};

const EMPTY_METRICS: SimMetrics = {
  klDivergence: null,
  convergenceSpeed: null,
  lpProfitability: null,
  manipulationCost: null,
  maxDrawdown: 0,
  rollingVol: 0,
  twap: 0,
  slippage: null,
  exitability: null,
  compositeScore: 0,
  stressScore: 0,
  sandwichBundlesLanded: 0,
  sandwichBundlesSubmitted: 0,
  sandwichRealizedEvLamports: 0,
  tickCrossings: 0,
};

export function fromApiMetrics(raw: ApiMetricsResponse | null | undefined): SimMetrics {
  const metrics = raw?.metrics || (raw as Record<string, unknown> | null | undefined);
  const out: SimMetrics = { ...EMPTY_METRICS };
  if (!metrics || typeof metrics !== "object") return out;
  for (const [key, value] of Object.entries(metrics)) {
    const mapped = FIELD_MAP[key];
    if (mapped && typeof value === "number" && Number.isFinite(value)) {
      (out as Record<keyof SimMetrics, number | null>)[mapped] = value;
    }
  }
  return out;
}

export { metricsFromResult } from "@/lib/api/adapters/runs";
