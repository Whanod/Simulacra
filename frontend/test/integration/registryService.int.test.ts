import { describe, it, expect } from "vitest";
import { registryService } from "@/lib/services/registryService";

// US-016: this integration test enforces the extensibility
// guarantees of the adapter against the real backend. It does NOT
// pin a fixed category count or a fixed closed set — new backend
// categories and types must land in the UI without any frontend
// test rewrite.

describe("registryService (integration)", () => {
  it("getCategories returns at least the built-in category set", async () => {
    const categories = await registryService.getCategories();
    const keys = categories.map((c) => c.key);
    // Built-in categories the backend ships today must still be
    // discoverable; the list is allowed to grow.
    for (const builtin of [
      "reg-markets",
      "reg-agents",
      "reg-clocks",
      "reg-ordering",
      "reg-gas",
      "reg-fees",
      "reg-feeds",
      "reg-exec",
      "reg-information",
    ]) {
      expect(keys).toContain(builtin);
    }
  });

  it("every returned category carries a non-empty entries list", async () => {
    const categories = await registryService.getCategories();
    for (const cat of categories) {
      expect(cat.entries.length).toBeGreaterThan(0);
    }
  });

  it("markets entries include CFAMM with the backend-provided description", async () => {
    const markets = await registryService.getCategory("reg-markets");
    expect(markets).toBeDefined();
    const cfamm = markets!.entries.find((e) =>
      e.name.toLowerCase().includes("constant function"),
    );
    expect(cfamm).toBeDefined();
    expect(cfamm!.description).toContain("constant-function AMM");
  });

  it("every backend type currently shipped has a non-empty description from the backend", async () => {
    // Post-cutover: descriptions come from the enriched /registry
    // response, NOT from a frontend coverage map. If a new type has
    // no backend description, that is a backend authoring gap and
    // the test flags it — but the frontend no longer owns the map.
    const categories = await registryService.getCategories();
    const missing: { category: string; type: string }[] = [];
    for (const cat of categories) {
      for (const entry of cat.entries) {
        if (!entry.description) {
          missing.push({ category: cat.label, type: entry.name });
        }
      }
    }
    expect(
      missing,
      `Backend entries with empty description (set description in BE metadata): ${missing
        .map((m) => `${m.category}/${m.type}`)
        .join(", ")}`,
    ).toEqual([]);
  });

  it("getCategory('reg-clocks') returns the clocks category", async () => {
    const clocks = await registryService.getCategory("reg-clocks");
    expect(clocks).toBeDefined();
    expect(clocks!.label).toBe("Clocks");
    expect(clocks!.entries.length).toBeGreaterThan(0);
  });

  it("unknown categories returned by the backend render without errors", async () => {
    // This asserts the negative: if the backend ever adds a category
    // the frontend has never heard of, the adapter must surface it
    // rather than dropping it. We can't mock a "new" category from
    // the integration side (the backend is real), so we lean on the
    // discovered list: every returned category must carry a label
    // and entries, even for categories the frontend doesn't special-
    // case anywhere.
    const categories = await registryService.getCategories();
    for (const cat of categories) {
      expect(cat.key).toBeTruthy();
      expect(cat.label).toBeTruthy();
      expect(Array.isArray(cat.entries)).toBe(true);
    }
  });
});
