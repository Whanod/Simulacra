import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  backendCategoryForKey,
  fromApiRegistry,
  fromApiRegistryCategory,
  type ApiRegistryCategoryResponse,
  type ApiRegistryListResponse,
} from "@/lib/api/adapters/registry";
import type {
  RegistryContractResponse,
  RegistryEntityDefinition,
} from "@/lib/types/contract";

const FIXTURES = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "..",
  "test",
  "fixtures",
  "api",
);

function loadFixture<T>(name: string): T {
  return JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8")) as T;
}

function makeEntity(
  partial: Partial<RegistryEntityDefinition> & {
    category: string;
    type: string;
    label: string;
  },
): RegistryEntityDefinition {
  return { builderSupported: true, ...partial };
}

describe("registry adapter", () => {
  const raw = loadFixture<ApiRegistryListResponse>("registry_list.json");

  describe("fromApiRegistry", () => {
    // US-016: tests are deliberately extensibility-first. We assert
    // that the adapter produces categories in backend-declared order
    // and that built-in categories are present — but we do not pin
    // the exact count or the exact set, so new backend categories
    // land in the frontend without a test rewrite.
    it("produces at least the built-in category set", () => {
      const categories = fromApiRegistry(raw);
      const keys = categories.map((c) => c.key);
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

    it("preserves backend-declared order for the built-in categories", () => {
      const categories = fromApiRegistry(raw);
      const keys = categories.map((c) => c.key);
      // The adapter sorts by the backend `order` field. For the
      // built-in set we know the relative order; we assert that
      // relation rather than the exact full list, so a new backend
      // category inserted anywhere doesn't break the test.
      const marketsIdx = keys.indexOf("reg-markets");
      const agentsIdx = keys.indexOf("reg-agents");
      const infoIdx = keys.indexOf("reg-information");
      expect(marketsIdx).toBeGreaterThanOrEqual(0);
      expect(agentsIdx).toBeGreaterThan(marketsIdx);
      expect(infoIdx).toBeGreaterThan(agentsIdx);
    });

    it("surfaces the backend-provided cfamm label, description, and badges", () => {
      const cats = fromApiRegistry(raw);
      const markets = cats.find((c) => c.key === "reg-markets")!;
      expect(markets.label).toBe("Markets");
      const cfamm = markets.entries.find((e) => e.name === "Constant Function AMM");
      expect(cfamm).toBeDefined();
      expect(cfamm!.description).toContain("constant-function AMM");
      expect(cfamm!.badges?.length).toBeGreaterThan(0);
    });

    it("marks builderSupported=false entities as disabled", () => {
      const cats = fromApiRegistry(raw);
      const feeds = cats.find((c) => c.key === "reg-feeds")!;
      const historical = feeds.entries.find((e) => e.name === "Historical Feed");
      expect(historical?.disabled).toBe(true);
    });

    it("preserves an unknown backend category emitted by the server", () => {
      const synthetic: RegistryContractResponse = {
        contractVersion: "v2",
        categories: [
          {
            key: "reg-markets",
            label: "Markets",
            order: 0,
            entities: [
              makeEntity({
                category: "markets",
                type: "cfamm",
                label: "Constant Function AMM",
              }),
            ],
          },
          {
            key: "reg-experimental_thing",
            label: "Experimental Thing",
            order: 99,
            entities: [
              makeEntity({
                category: "experimental_thing",
                type: "alpha",
                label: "Alpha",
              }),
              makeEntity({
                category: "experimental_thing",
                type: "beta",
                label: "Beta",
              }),
            ],
          },
        ],
      };
      const cats = fromApiRegistry(synthetic);
      expect(cats).toHaveLength(2);
      const unknown = cats.find((c) => c.key === "reg-experimental_thing");
      expect(unknown?.label).toBe("Experimental Thing");
      expect(unknown?.entries.map((e) => e.name)).toEqual(["Alpha", "Beta"]);
    });
  });

  describe("fromApiRegistryCategory", () => {
    it("maps a single-category response", () => {
      const body = loadFixture<ApiRegistryCategoryResponse>(
        "registry_markets.json",
      );
      const cat = fromApiRegistryCategory(body, "reg-markets");
      expect(cat.key).toBe("reg-markets");
      expect(cat.label).toBe("Markets");
      expect(cat.entries.length).toBeGreaterThan(0);
      expect(cat.entries.some((e) => e.name === "Constant Function AMM")).toBe(
        true,
      );
    });
  });

  describe("backendCategoryForKey", () => {
    it("maps each RegTab to its backend key", () => {
      expect(backendCategoryForKey("reg-markets")).toBe("markets");
      expect(backendCategoryForKey("reg-clocks")).toBe("clocks");
      expect(backendCategoryForKey("reg-information")).toBe("information_filters");
      expect(backendCategoryForKey("reg-exec")).toBe("execution_models");
    });
  });
});
