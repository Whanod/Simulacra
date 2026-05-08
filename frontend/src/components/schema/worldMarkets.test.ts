import { describe, expect, it } from "vitest";
import {
  makeBlockId,
  readBlocks,
  readLinks,
  sanitizeBlock,
  sanitizeLink,
  seedLayout,
} from "./worldMarkets";

describe("sanitizeBlock", () => {
  it("fills missing fields with defaults and honors the fallback id", () => {
    const block = sanitizeBlock({}, "m7");
    expect(block.id).toBe("m7");
    expect(block.type).toBe("cfamm");
    expect(block.label).toBe("CFAMM-m7");
    expect(block.tokens).toEqual(["TKN-A", "TKN-B"]);
  });

  it("keeps user-provided id/type/label/tokens", () => {
    const block = sanitizeBlock(
      { id: "x", type: "clob", label: "My CLOB", tokens: ["ETH", "USDC"] },
      "m1",
    );
    expect(block).toEqual({
      id: "x",
      type: "clob",
      label: "My CLOB",
      tokens: ["ETH", "USDC"],
    });
  });

  it("drops non-string token entries silently", () => {
    const block = sanitizeBlock(
      { id: "m1", tokens: ["A", 2, null, "B"] },
      "m1",
    );
    expect(block.tokens).toEqual(["A", "B"]);
  });

  it("yields a valid block even for completely bogus input", () => {
    const block = sanitizeBlock(42, "m9");
    expect(block.id).toBe("m9");
    expect(block.type).toBe("cfamm");
    expect(block.label).toBe("CFAMM-m9");
  });
});

describe("sanitizeLink", () => {
  it("returns null for links missing from/to", () => {
    expect(sanitizeLink({ to: "m2", token: "USDC" })).toBeNull();
    expect(sanitizeLink({ from: "m1", token: "USDC" })).toBeNull();
    expect(sanitizeLink("not an object")).toBeNull();
  });

  it("returns a normalized link when from/to are present", () => {
    expect(sanitizeLink({ from: "a", to: "b", token: "X" })).toEqual({
      from: "a",
      to: "b",
      token: "X",
    });
  });

  it("defaults token to empty string when absent", () => {
    expect(sanitizeLink({ from: "a", to: "b" })).toEqual({
      from: "a",
      to: "b",
      token: "",
    });
  });
});

describe("readBlocks / readLinks", () => {
  it("returns [] when the params object has no markets/links", () => {
    expect(readBlocks({})).toEqual([]);
    expect(readLinks({})).toEqual([]);
  });

  it("normalizes arrays and drops malformed links", () => {
    const params = {
      markets: [{ id: "a" }, { id: "b", type: "clob" }],
      links: [
        { from: "a", to: "b", token: "USDC" },
        { from: "a" }, // missing `to` — dropped
        null, // not an object — dropped
      ],
    };
    expect(readBlocks(params)).toHaveLength(2);
    const links = readLinks(params);
    expect(links).toHaveLength(1);
    expect(links[0]).toEqual({ from: "a", to: "b", token: "USDC" });
  });

  it("survives non-array markets/links without throwing", () => {
    expect(readBlocks({ markets: "oops" })).toEqual([]);
    expect(readLinks({ links: 42 })).toEqual([]);
  });
});

describe("makeBlockId", () => {
  it("picks the next available m{n} id", () => {
    const blocks = [
      { id: "m1", type: "cfamm", label: "", tokens: [] },
      { id: "m2", type: "cfamm", label: "", tokens: [] },
    ];
    expect(makeBlockId(blocks)).toBe("m3");
  });

  it("skips taken ids even when they land mid-sequence", () => {
    const blocks = [
      { id: "m1", type: "cfamm", label: "", tokens: [] },
      { id: "m3", type: "cfamm", label: "", tokens: [] },
    ];
    // existing.length + 1 == 3, which is taken → bumps to m4
    expect(makeBlockId(blocks)).toBe("m4");
  });

  it("returns m1 for an empty list", () => {
    expect(makeBlockId([])).toBe("m1");
  });
});

describe("seedLayout", () => {
  it("returns a deterministic 3-column grid indexed by block id", () => {
    const blocks = [
      { id: "a", type: "cfamm", label: "", tokens: [] },
      { id: "b", type: "cfamm", label: "", tokens: [] },
      { id: "c", type: "cfamm", label: "", tokens: [] },
      { id: "d", type: "cfamm", label: "", tokens: [] },
    ];
    const layout = seedLayout(blocks, 100, 50);
    expect(layout.a).toEqual({ x: 40, y: 40 });
    expect(layout.b).toEqual({ x: 140, y: 40 });
    expect(layout.c).toEqual({ x: 240, y: 40 });
    // 4th block wraps to next row.
    expect(layout.d).toEqual({ x: 40, y: 90 });
  });
});
