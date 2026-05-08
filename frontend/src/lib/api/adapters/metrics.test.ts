import { describe, it, expect } from "vitest";
import { fromApiMetrics, metricsFromResult } from "@/lib/api/adapters/metrics";

describe("metrics adapter", () => {
  describe("fromApiMetrics", () => {
    it("maps snake_case keys to SimMetrics fields", () => {
      const out = fromApiMetrics({
        metrics: {
          kl_divergence: 0.1,
          convergence_speed: 12,
          lp_profitability: 1.5,
          manipulation_cost: 200,
          max_drawdown: -3.5,
          rolling_vol: 0.02,
          twap: 1.01,
          slippage: 0.003,
          exitability: 95,
          composite_score: 0.82,
        },
      });
      expect(out.klDivergence).toBe(0.1);
      expect(out.convergenceSpeed).toBe(12);
      expect(out.lpProfitability).toBe(1.5);
      expect(out.manipulationCost).toBe(200);
      expect(out.maxDrawdown).toBe(-3.5);
      expect(out.rollingVol).toBe(0.02);
      expect(out.twap).toBe(1.01);
      expect(out.slippage).toBe(0.003);
      expect(out.exitability).toBe(95);
      expect(out.compositeScore).toBe(0.82);
    });

    it("also accepts a flat top-level object", () => {
      const out = fromApiMetrics({ twap: 2, slippage: 1 } as never);
      expect(out.twap).toBe(2);
      expect(out.slippage).toBe(1);
      expect(out.klDivergence).toBeNull();
    });

    it("returns zero defaults when raw is null", () => {
      const out = fromApiMetrics(null);
      expect(out.twap).toBe(0);
      expect(out.compositeScore).toBe(0);
    });
  });

  describe("metricsFromResult", () => {
    it("derives twap from price_history", () => {
      const out = metricsFromResult({
        price_history: [{ TKN: 100 }, { TKN: 110 }, { TKN: 90 }],
        liquidity_history: [100, 200],
      });
      expect(out.twap).toBeCloseTo(100);
      expect(out.maxDrawdown).toBeLessThan(0);
      expect(out.lpProfitability).toBe(2);
      expect(out.compositeScore).toBeGreaterThan(0);
    });

    it("returns null lpProfitability when the pool starts at zero liquidity", () => {
      const out = metricsFromResult({
        price_history: [{ TKN: 100 }, { TKN: 100 }],
        liquidity_history: [0, 50],
      });
      expect(out.lpProfitability).toBeNull();
      expect(Number.isFinite(out.compositeScore)).toBe(true);
    });

    it("handles empty history", () => {
      const out = metricsFromResult({});
      expect(out.twap).toBe(0);
      expect(out.maxDrawdown).toBe(0);
      expect(out.compositeScore).toBe(0);
    });

    it("reads derived metrics from metadata.derived_metrics", () => {
      const out = metricsFromResult({
        price_history: [{ TKN: 100 }, { TKN: 110 }],
        metadata: {
          derived_metrics: {
            kl_divergence: 0.42,
            convergence_speed: 7,
            manipulation_cost: 1234.5,
            slippage: 0.012,
            exitability: 0.88,
          },
        },
      });
      expect(out.klDivergence).toBe(0.42);
      expect(out.convergenceSpeed).toBe(7);
      expect(out.manipulationCost).toBe(1234.5);
      expect(out.slippage).toBe(0.012);
      expect(out.exitability).toBe(0.88);
    });

    it("treats null/missing derived metrics as null", () => {
      const out = metricsFromResult({
        price_history: [{ TKN: 100 }, { TKN: 110 }],
        metadata: {
          derived_metrics: {
            kl_divergence: null,
            slippage: null,
          },
        },
      });
      expect(out.klDivergence).toBeNull();
      expect(out.convergenceSpeed).toBeNull();
      expect(out.slippage).toBeNull();
      expect(out.exitability).toBeNull();
    });
  });
});
