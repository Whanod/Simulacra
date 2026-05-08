import { describe, it, expect } from "vitest";
import {
  CALIBRATION_THRESHOLDS,
  EMPTY_CALIBRATION_BANDS,
  extractCalibrationBands,
  thresholdForMetric,
} from "./calibrationBands";

describe("thresholdForMetric", () => {
  it("returns the configured threshold for a known family metric", () => {
    expect(thresholdForMetric("pool_price")).toEqual({ relative: 0.005 });
  });

  it("returns thresholds for canonical replay chart keys", () => {
    expect(thresholdForMetric("bundle_landing_rate")).toEqual({
      absolute: 0.05,
    });
    expect(thresholdForMetric("tip_efficiency")).toEqual({ relative: 0.1 });
  });

  it("inherits the family threshold for per-pool / per-agent metrics", () => {
    expect(thresholdForMetric("pool_price:SOL/USDC")).toEqual({
      relative: 0.005,
    });
    expect(thresholdForMetric("lp_balance:lp-1")).toEqual({ relative: 0.005 });
  });

  it("returns the absolute threshold for count metrics", () => {
    expect(thresholdForMetric("liquidations_triggered")).toEqual({
      absolute: 1,
    });
  });

  it("returns null for an unknown metric key", () => {
    expect(thresholdForMetric("totally_made_up")).toBeNull();
  });

  it("covers every metric family in CALIBRATION_THRESHOLDS", () => {
    for (const key of Object.keys(CALIBRATION_THRESHOLDS)) {
      const t = thresholdForMetric(key);
      expect(t).not.toBeNull();
    }
  });
});

describe("extractCalibrationBands", () => {
  it("returns empty bands for a missing result", () => {
    expect(extractCalibrationBands(null)).toBe(EMPTY_CALIBRATION_BANDS);
    expect(extractCalibrationBands(undefined)).toBe(EMPTY_CALIBRATION_BANDS);
  });

  it("returns empty bands when replay_diff is null/missing", () => {
    expect(extractCalibrationBands({ price_history: [] })).toEqual(
      EMPTY_CALIBRATION_BANDS,
    );
    expect(extractCalibrationBands({ replay_diff: null })).toEqual(
      EMPTY_CALIBRATION_BANDS,
    );
  });

  it("parses a flat per-metric ErrorBand map", () => {
    const out = extractCalibrationBands({
      replay_diff: {
        tips_paid: {
          metric: "tips_paid",
          predicted: 1100,
          actual: 1000,
          abs_error: 100,
          rel_error: 0.1,
          supported: true,
        },
        "pool_price:SOL/USDC": {
          metric: "pool_price:SOL/USDC",
          predicted: 145.5,
          actual: 145.0,
          abs_error: 0.5,
          rel_error: 0.5 / 145.0,
          supported: true,
        },
      },
    });
    expect(out.byMetric.tips_paid?.predicted).toBe(1100);
    expect(out.byMetric["pool_price:SOL/USDC"]?.actual).toBe(145.0);
    expect(out.family.tips_paid?.metric).toBe("tips_paid");
    expect(out.family.pool_price?.metric).toBe("pool_price:SOL/USDC");
  });

  it("accepts the {per_metric_error: {...}} envelope", () => {
    const out = extractCalibrationBands({
      replay_diff: {
        per_metric_error: {
          bundle_landing_rate: {
            metric: "bundle_landing_rate",
            predicted: 0.9,
            actual: 1.0,
            absolute_error: 0.1,
            relative_error: 0.1,
            supported: true,
          },
        },
      },
    });
    expect(out.byMetric.bundle_landing_rate?.predicted).toBe(0.9);
    expect(out.byMetric.bundle_landing_rate?.abs_error).toBe(0.1);
    expect(out.family.bundle_landing_rate?.metric).toBe("bundle_landing_rate");
  });

  it("marks unsupported bands when actual is null", () => {
    const out = extractCalibrationBands({
      replay_diff: {
        liquidations_triggered: {
          metric: "liquidations_triggered",
          predicted: 4,
          actual: null,
          supported: false,
        },
      },
    });
    expect(out.byMetric.liquidations_triggered?.supported).toBe(false);
  });
});
