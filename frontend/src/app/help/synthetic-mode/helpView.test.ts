import { describe, it, expect } from "vitest";
import { syntheticHelpView } from "./helpView";
import type { SimTemplate } from "@/lib/api/adapters/templates";

function tmpl(overrides: Partial<SimTemplate>): SimTemplate {
  return {
    id: "t1",
    name: "T1",
    description: "",
    category: "General",
    spec: {},
    editableFields: [],
    recommendedMetrics: [],
    syntheticMode: true,
    syntheticMathModel: "l2_norm_cfamm",
    nonTransferableConclusions: ["caveat one"],
    featured: false,
    rawSpec: {},
    requiresRawSpec: false,
    ...overrides,
  };
}

describe("syntheticHelpView", () => {
  it("returns empty sections when no templates", () => {
    const v = syntheticHelpView([]);
    expect(v.templateSections).toEqual([]);
    // Phase 0 model defaults are still present so the page is not empty
    expect(v.mathModelSections.map((s) => s.id)).toContain("l2_norm_cfamm");
    expect(v.mathModelSections.map((s) => s.id)).toContain("clob");
  });

  it("produces a section per synthetic template with named math model", () => {
    const v = syntheticHelpView([
      tmpl({
        id: "whirlpool-fee-tuning",
        name: "Whirlpool fee tuning",
        syntheticMathModel: "l2_norm_cfamm",
        nonTransferableConclusions: ["fee-tier rankings may flip"],
      }),
      tmpl({
        id: "raydium-vs-whirlpool-arb",
        name: "Raydium vs Whirlpool arbitrage",
        syntheticMathModel: "l2_norm_cfamm",
        nonTransferableConclusions: ["arb sizing differs"],
      }),
    ]);
    expect(v.templateSections.map((s) => s.id)).toEqual([
      "whirlpool-fee-tuning",
      "raydium-vs-whirlpool-arb",
    ]);
    expect(v.templateSections[0].mathModelDisplayName).toBe("L2-norm CFAMM");
    expect(v.templateSections[0].conclusions[0]).toBe(
      "fee-tier rankings may flip",
    );
  });

  it("skips non-synthetic templates", () => {
    const v = syntheticHelpView([
      tmpl({ id: "real", syntheticMode: false }),
      tmpl({ id: "fake", syntheticMode: true }),
    ]);
    expect(v.templateSections.map((s) => s.id)).toEqual(["fake"]);
  });

  it("explains the L2-norm invariant in plain language", () => {
    const v = syntheticHelpView([]);
    const l2 = v.mathModelSections.find((s) => s.id === "l2_norm_cfamm");
    expect(l2).toBeDefined();
    expect(l2!.invariantPlain).toMatch(/L − rᵢ|prediction-market/i);
  });

  it("includes math models referenced by templates even if outside phase-0 defaults", () => {
    const v = syntheticHelpView([
      tmpl({ id: "future-clmm", syntheticMathModel: "clmm" }),
    ]);
    expect(v.mathModelSections.map((s) => s.id)).toContain("clmm");
  });
});
