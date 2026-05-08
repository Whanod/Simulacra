import { describe, expect, it } from "vitest";

import {
  formatMetricValue,
  METRIC_META,
  parseMetricKey,
} from "./metricMeta";

describe("parseMetricKey", () => {
  it("returns null for unknown keys", () => {
    expect(parseMetricKey("unknown_metric")).toBeNull();
  });

  it("parses a bare metric name", () => {
    const parsed = parseMetricKey("range_il");
    expect(parsed).not.toBeNull();
    expect(parsed!.base).toBe("range_il");
    expect(parsed!.variant).toBeUndefined();
    expect(parsed!.label).toBe(METRIC_META.range_il.label);
  });

  it("splits ``base:variant`` on the first colon", () => {
    const parsed = parseMetricKey("lp_in_range_fraction:lp-tight");
    expect(parsed).not.toBeNull();
    expect(parsed!.base).toBe("lp_in_range_fraction");
    expect(parsed!.variant).toBe("lp-tight");
    expect(parsed!.label).toContain("lp-tight");
  });

  it("preserves later colons inside the variant", () => {
    // The engine never emits multi-colon keys today, but the parser
    // should be tolerant in case an agent_id ever contains one.
    const parsed = parseMetricKey("range_il:agent:weird");
    expect(parsed).not.toBeNull();
    expect(parsed!.variant).toBe("agent:weird");
  });
});

describe("formatMetricValue", () => {
  it("renders percent with metric-specified digits", () => {
    expect(formatMetricValue(0.1234, METRIC_META.range_il)).toBe("12.34%");
  });

  it("renders ratio at metric digits", () => {
    expect(formatMetricValue(1.5, METRIC_META.fees_vs_il_breakeven)).toBe("1.50");
  });

  it("renders +Infinity as ∞ (fees_vs_il_breakeven sentinel)", () => {
    expect(
      formatMetricValue(Number.POSITIVE_INFINITY, METRIC_META.fees_vs_il_breakeven),
    ).toBe("∞");
  });

  it("renders integer metrics as whole numbers", () => {
    expect(formatMetricValue(12.7, METRIC_META.convergence_speed)).toBe("13");
  });

  it("returns em-dash for NaN", () => {
    expect(formatMetricValue(Number.NaN, METRIC_META.range_il)).toBe("—");
  });
});
