import { describe, expect, it } from "vitest";
import { draftFromApiSpec, draftToApiSpec } from "@/lib/api/adapters/drafts";
import { parseRawSpecText, serializeDraftToText } from "./rawSpecIo";

function baseSpec() {
  return {
    market: { type: "cfamm", num_assets: 2, fee_bps: 30 },
    clock: { type: "block", block_time: 12 },
    execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
    fee_model: { type: "flat", rate_bps: 30 },
    feeds: [
      { type: "stochastic", drift: 0.05, volatility: 0.2, initial_price: 1 },
    ],
    agents: {
      total: 10,
      role_params: {
        noise: { trade_min: 10, trade_max: 100 },
      },
    },
    config: {
      num_rounds: 100,
      seed: 42,
      information_filter: "full_transparency",
    },
  };
}

describe("parseRawSpecText (US-014)", () => {
  it("rejects empty input", () => {
    expect(parseRawSpecText("")).toEqual({
      ok: false,
      error: "Spec cannot be empty",
    });
    expect(parseRawSpecText("   \n  ")).toEqual({
      ok: false,
      error: "Spec cannot be empty",
    });
  });

  it("rejects invalid JSON without mutating anything", () => {
    const result = parseRawSpecText("{ not-json: 1,");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.length).toBeGreaterThan(0);
    }
  });

  it("rejects JSON that is not a plain object", () => {
    expect(parseRawSpecText("[1, 2, 3]").ok).toBe(false);
    expect(parseRawSpecText("null").ok).toBe(false);
    expect(parseRawSpecText('"just a string"').ok).toBe(false);
    expect(parseRawSpecText("42").ok).toBe(false);
  });

  it("parses a valid spec into a draft", () => {
    const text = JSON.stringify(baseSpec());
    const result = parseRawSpecText(text, { name: "my-sim", id: "abc" });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.draft.name).toBe("my-sim");
      expect(result.draft.id).toBe("abc");
      // The draft must surface the market entity that draftFromApiSpec
      // collects, proving the raw editor is backed by the same draft
      // model used by the structured path.
      expect(result.draft.entities.some((e) => e.configPath === "market")).toBe(
        true,
      );
    }
  });

  it("round-trips an unknown top-level block through raw editing", () => {
    // The purpose of US-014 is to let power users edit arbitrary
    // backend-defined data. Simulate a user pasting a spec that has
    // a vendor extension the structured form can't represent.
    const spec = {
      ...baseSpec(),
      vendor_ext: { hello: "world", nested: { flag: true } },
    };
    const result = parseRawSpecText(JSON.stringify(spec));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const roundTripped = draftToApiSpec(result.draft);
    expect(roundTripped).toEqual(spec);
  });

  it("round-trips an unknown backend market type through raw editing", () => {
    const spec = {
      ...baseSpec(),
      market: {
        type: "unknown_future_market",
        bespoke_knob: "keep me",
        nested: { a: 1 },
      },
    };
    const result = parseRawSpecText(JSON.stringify(spec));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(draftToApiSpec(result.draft)).toEqual(spec);
  });
});

describe("serializeDraftToText", () => {
  it("serializes a draft as pretty-printed JSON", () => {
    const draft = draftFromApiSpec(baseSpec());
    const text = serializeDraftToText(draft);
    expect(text).toContain("\n");
    const parsedBack = JSON.parse(text);
    expect(parsedBack).toEqual(baseSpec());
  });

  it("is the inverse of parseRawSpecText for unknown-laden specs", () => {
    // Entering raw mode serializes the structured draft; editing and
    // re-parsing must preserve everything the draft model holds.
    const spec = {
      ...baseSpec(),
      vendor_ext: { nested: [1, 2, 3] },
      market: { type: "exotic", keep: "yes" },
    };
    const initial = draftFromApiSpec(spec);
    const text = serializeDraftToText(initial);

    const reparsed = parseRawSpecText(text);
    expect(reparsed.ok).toBe(true);
    if (!reparsed.ok) return;
    expect(draftToApiSpec(reparsed.draft)).toEqual(spec);
  });
});
