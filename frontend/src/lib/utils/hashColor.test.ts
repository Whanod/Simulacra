import { describe, expect, it } from "vitest";
import { hashColorVar, hashEventClass } from "./hashColor";

describe("hashColorVar (US-015)", () => {
  it("returns a CSS var from the palette", () => {
    const color = hashColorVar("noise");
    expect(color).toMatch(/^var\(--[a-z]+\)$/);
  });

  it("is stable for the same key", () => {
    expect(hashColorVar("informed")).toBe(hashColorVar("informed"));
    expect(hashColorVar("unknown_future_role")).toBe(
      hashColorVar("unknown_future_role"),
    );
  });

  it("distinguishes different keys often enough to avoid a single-bucket palette", () => {
    const keys = [
      "noise",
      "informed",
      "arbitrageur",
      "manipulator",
      "passive_lp",
      "rebalancing_lp",
      "whale",
      "front_runner",
      "jit_lp",
      "exotic_future_role",
    ];
    const colors = new Set(keys.map(hashColorVar));
    // With a 7-color palette and 10 distinct keys, we should land on
    // at least 3 distinct colors in practice. The exact number depends
    // on the hash, but collapsing to one bucket would defeat the
    // purpose of the helper.
    expect(colors.size).toBeGreaterThanOrEqual(3);
  });

  it("handles empty and single-char keys", () => {
    expect(hashColorVar("")).toMatch(/^var\(--[a-z]+\)$/);
    expect(hashColorVar("a")).toMatch(/^var\(--[a-z]+\)$/);
  });
});

describe("hashEventClass (US-015)", () => {
  it("returns one of the known event-log CSS classes", () => {
    const allowed = new Set(["trade", "lp", "oracle", "reward", "fail"]);
    expect(allowed.has(hashEventClass("ACTION_EXECUTED"))).toBe(true);
    expect(allowed.has(hashEventClass("UNKNOWN_FUTURE_EVENT"))).toBe(true);
  });

  it("is stable for the same key", () => {
    expect(hashEventClass("ORACLE_UPDATE")).toBe(hashEventClass("ORACLE_UPDATE"));
    expect(hashEventClass("VENDOR_SPECIFIC")).toBe(
      hashEventClass("VENDOR_SPECIFIC"),
    );
  });
});
