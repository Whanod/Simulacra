import { describe, it, expect } from "vitest";
import { syntheticBadgeView } from "./syntheticBadgeView";

describe("syntheticBadgeView", () => {
  it("hides the badge when template is null", () => {
    const v = syntheticBadgeView(null);
    expect(v.visible).toBe(false);
  });

  it("hides the badge when syntheticMode is false", () => {
    const v = syntheticBadgeView({
      syntheticMode: false,
      syntheticMathModel: "l2_norm_cfamm",
      nonTransferableConclusions: ["irrelevant"],
    });
    expect(v.visible).toBe(false);
  });

  it("names the L2-norm CFAMM math model in the label", () => {
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "l2_norm_cfamm",
      nonTransferableConclusions: ["fee tiers may flip"],
    });
    expect(v.visible).toBe(true);
    expect(v.label).toContain("L2-norm CFAMM");
    expect(v.label).not.toMatch(/^Synthetic math$/);
    expect(v.mathModel).toBe("l2_norm_cfamm");
  });

  it("names the CLOB math model in the label", () => {
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "clob",
      nonTransferableConclusions: ["generic clob caveat"],
    });
    expect(v.label.toLowerCase()).toMatch(/clob|order book/);
  });

  it("uses the first non-transferable conclusion as tooltip", () => {
    const conclusion =
      "Fee-tier rankings may flip on real Whirlpool CLMM. Do not use to pick a mainnet fee tier.";
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "l2_norm_cfamm",
      nonTransferableConclusions: [conclusion, "second"],
    });
    expect(v.tooltip).toBe(conclusion);
  });

  it("falls back to a generic tooltip when no conclusions are provided", () => {
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "l2_norm_cfamm",
      nonTransferableConclusions: [],
    });
    expect(v.tooltip).toMatch(/synthetic|mainnet/i);
    expect(v.tooltip.length).toBeGreaterThan(0);
  });

  it("links to the in-studio help route", () => {
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "l2_norm_cfamm",
      nonTransferableConclusions: [],
    });
    expect(v.helpHref).toBe("/help/synthetic-mode");
  });

  it("falls back to a generic label when math model is unknown", () => {
    const v = syntheticBadgeView({
      syntheticMode: true,
      syntheticMathModel: "mystery_amm",
      nonTransferableConclusions: ["x"],
    });
    expect(v.visible).toBe(true);
    expect(v.label.length).toBeGreaterThan(0);
  });
});
