import { describe, expect, it } from "vitest";
import {
  SUPPORTED_CONTRACT_VERSION,
  isSupportedContractVersion,
  type EntityUiSchema,
  type RegistryContractResponse,
  type RegistryEntityDefinition,
  type UiFieldMeta,
} from "./contract";

describe("registry contract types", () => {
  it("accepts a minimal entity definition with only label and builderSupported", () => {
    const entity: RegistryEntityDefinition = {
      category: "markets",
      type: "cfamm",
      label: "Constant Function AMM",
      builderSupported: true,
    };
    expect(entity.label).toBe("Constant Function AMM");
    expect(entity.schema).toBeUndefined();
    expect(entity.uiSchema).toBeUndefined();
    expect(entity.defaults).toBeUndefined();
  });

  it("accepts an enriched entity with schema, uiSchema, defaults, and per-entity colorHint", () => {
    const uiSchema: EntityUiSchema = {
      sections: [
        { key: "basic", label: "Basic", fields: ["drift", "volatility"] },
      ],
      fields: {
        drift: { label: "Drift", widget: "slider", min: 0, max: 1, step: 0.01 },
        volatility: {
          label: "Volatility",
          widget: "number",
          helpText: "Annualized σ",
        },
      },
    };
    const entity: RegistryEntityDefinition = {
      category: "feeds",
      type: "stochastic",
      label: "Stochastic",
      builderSupported: true,
      colorHint: "#7c3aed",
      schema: {
        type: "object",
        properties: {
          drift: { type: "number" },
          volatility: { type: "number" },
        },
      },
      defaults: { drift: 0.05, volatility: 0.2 },
      uiSchema,
    };
    expect(entity.uiSchema?.fields?.drift.widget).toBe("slider");
    expect(entity.colorHint).toBe("#7c3aed");
  });

  it("allows per-field colorHint with enum labels", () => {
    const meta: UiFieldMeta = {
      widget: "select",
      enumLabels: { up: "Up", down: "Down" },
      colorHint: "#22c55e",
    };
    expect(meta.enumLabels?.up).toBe("Up");
  });

  it("supports specialEditor for composite structures", () => {
    const uiSchema: EntityUiSchema = {
      specialEditor: "world-markets-graph",
    };
    expect(uiSchema.specialEditor).toBe("world-markets-graph");
  });

  it("reports contract version support", () => {
    expect(isSupportedContractVersion(SUPPORTED_CONTRACT_VERSION)).toBe(true);
    expect(isSupportedContractVersion("v999")).toBe(false);
  });

  it("round-trips a contract response through JSON without loss", () => {
    const response: RegistryContractResponse = {
      contractVersion: SUPPORTED_CONTRACT_VERSION,
      categories: [
        {
          key: "reg-markets",
          label: "Markets",
          entities: [
            {
              category: "markets",
              type: "unknown_future_type",
              label: "Unknown Future Type",
              builderSupported: false,
              metadata: { origin: "backend-only" },
            },
          ],
        },
      ],
    };
    const roundTrip = JSON.parse(
      JSON.stringify(response),
    ) as RegistryContractResponse;
    expect(roundTrip.categories[0].entities[0].builderSupported).toBe(false);
    expect(roundTrip.categories[0].entities[0].metadata?.origin).toBe(
      "backend-only",
    );
  });
});
