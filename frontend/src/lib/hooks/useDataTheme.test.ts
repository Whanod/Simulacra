// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { dataThemeFromSpec } from "./useDataTheme";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

describe("dataThemeFromSpec", () => {
  it("returns_solana_for_solana_like", () => {
    expect(dataThemeFromSpec({ execution: { model: "solana_like" } })).toBe(
      "solana",
    );
  });

  it("returns_neutral_for_other_models", () => {
    expect(dataThemeFromSpec({ execution: { model: "direct" } })).toBe(
      "neutral",
    );
    expect(dataThemeFromSpec({ execution: { model: "batch" } })).toBe(
      "neutral",
    );
    expect(dataThemeFromSpec({ execution: {} })).toBe("neutral");
    expect(dataThemeFromSpec(null)).toBe("neutral");
    expect(dataThemeFromSpec(undefined)).toBe("neutral");
  });
});
