export interface MathModelInfo {
  id: string;
  badgeLabel: string;
  displayName: string;
  invariantPlain: string;
}

export const MATH_MODELS: Record<string, MathModelInfo> = {
  l2_norm_cfamm: {
    id: "l2_norm_cfamm",
    badgeLabel: "Synthetic math: L2-norm CFAMM (not xy=k or CLMM)",
    displayName: "L2-norm CFAMM",
    invariantPlain:
      "Invariant is Σ(L − rᵢ)² = L². Prices are (L − rᵢ)² / L². Prediction-market shaped (LMSR-adjacent), not xy=k and not concentrated-liquidity.",
  },
  clob: {
    id: "clob",
    badgeLabel: "Synthetic math: generic order book",
    displayName: "Generic CLOB",
    invariantPlain:
      "Generic price-time-priority order book. No Solana-specific matching engine quirks (no Phoenix/OpenBook-specific behaviour).",
  },
  xy_k: {
    id: "xy_k",
    badgeLabel: "Synthetic math: xy=k calibration pending",
    displayName: "xy=k",
    invariantPlain:
      "Constant-product invariant x · y = k. Calibration against real Raydium AMM v4 pools is pending.",
  },
  clmm: {
    id: "clmm",
    badgeLabel: "Synthetic math: CLMM calibration pending",
    displayName: "CLMM",
    invariantPlain:
      "Concentrated-liquidity market maker with tick ranges. Calibration against real Whirlpool pools is pending.",
  },
  dlmm: {
    id: "dlmm",
    badgeLabel: "Synthetic math: DLMM calibration pending",
    displayName: "DLMM",
    invariantPlain:
      "Discrete-bin liquidity market maker. Calibration against real Meteora DLMM pools is pending.",
  },
};

export const MATH_MODEL_FALLBACK_LABEL = "Synthetic math (model unspecified)";

export function mathModelInfo(model: string | null | undefined): MathModelInfo | null {
  if (!model) return null;
  return MATH_MODELS[model] ?? null;
}

export function mathModelBadgeLabel(model: string | null | undefined): string {
  const info = mathModelInfo(model);
  return info ? info.badgeLabel : MATH_MODEL_FALLBACK_LABEL;
}
