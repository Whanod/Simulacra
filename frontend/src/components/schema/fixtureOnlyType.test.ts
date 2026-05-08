/**
 * US-016: schema-rendered editing for a fixture-only type.
 *
 * This test proves the schema-driven editing path is truly
 * extensible — a backend category and type the frontend has NEVER
 * hardcoded anywhere can still:
 *
 *   1. survive draftFromApiSpec (US-004 / US-006)
 *   2. carry a schema + uiSchema through the draft model (US-005)
 *   3. resolve through the schema fields pipeline (US-008 / US-009)
 *   4. serialize back through draftToApiSpec with no loss (US-006)
 *
 * The renderer component (SchemaForm) consumes `resolveFields` +
 * `groupIntoSections` + `validateField` output directly, so
 * exercising those pure helpers against a fixture-only type is
 * equivalent to exercising the renderer without a React transform.
 */

import { describe, expect, it } from "vitest";
import {
  filterByLevel,
  groupIntoSections,
  resolveFields,
  validateField,
} from "@/lib/schema/fields";
import {
  draftFromApiSpec,
  draftToApiSpec,
} from "@/lib/api/adapters/drafts";
import { parseRawSpecText } from "./rawSpecIo";
import type {
  EntityUiSchema,
  JsonSchema,
  RegistryContractResponse,
} from "@/lib/types/contract";
import { SUPPORTED_CONTRACT_VERSION } from "@/lib/types/contract";

// A synthetic category/type that exists nowhere in the frontend
// code. If hidden coverage maps slip back in, this test breaks.
const CATEGORY = "future_widgets";
const TYPE = "exotic_widget";

const SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    intensity: {
      type: "number",
      minimum: 0,
      maximum: 100,
      default: 42,
    },
    mode: {
      type: "string",
      enum: ["alpha", "beta", "gamma"],
      default: "beta",
    },
    enabled: {
      type: "boolean",
      default: true,
    },
  },
  required: ["intensity"],
};

const UI_SCHEMA: EntityUiSchema = {
  sections: [
    {
      key: "main",
      label: "Widget Parameters",
      order: 0,
      level: "basic",
      fields: ["intensity", "mode", "enabled"],
    },
  ],
  fields: {
    intensity: {
      label: "Intensity",
      widget: "slider",
      min: 0,
      max: 100,
      step: 1,
      helpText: "Widget amplitude",
    },
    mode: {
      label: "Mode",
      widget: "select",
      enumLabels: { alpha: "Alpha Wave", beta: "Beta Wave", gamma: "Gamma Wave" },
    },
    enabled: {
      label: "Enabled",
      widget: "switch",
    },
  },
};

const CONTRACT: RegistryContractResponse = {
  contractVersion: SUPPORTED_CONTRACT_VERSION,
  categories: [
    {
      key: `reg-${CATEGORY}`,
      label: "Future Widgets",
      entities: [
        {
          category: CATEGORY,
          type: TYPE,
          label: "Exotic Widget",
          builderSupported: true,
          schema: SCHEMA,
          uiSchema: UI_SCHEMA,
        },
      ],
    },
  ],
};

describe("fixture-only entity type (US-016)", () => {
  it("resolves fields from the fixture schema into the expected widget kinds", () => {
    const resolved = resolveFields(SCHEMA, UI_SCHEMA);
    const byKey = Object.fromEntries(resolved.map((f) => [f.key, f]));

    expect(byKey.intensity.kind).toBe("number");
    expect(byKey.intensity.widget).toBe("slider");
    expect(byKey.intensity.required).toBe(true);
    expect(byKey.intensity.label).toBe("Intensity");
    expect(byKey.intensity.helpText).toBe("Widget amplitude");

    expect(byKey.mode.kind).toBe("enum");
    expect(byKey.mode.enumValues).toEqual(["alpha", "beta", "gamma"]);
    expect(byKey.mode.enumLabels?.alpha).toBe("Alpha Wave");

    expect(byKey.enabled.kind).toBe("boolean");
    expect(byKey.enabled.widget).toBe("switch");
  });

  it("groups fixture fields into the backend-declared section", () => {
    const resolved = resolveFields(SCHEMA, UI_SCHEMA);
    const sections = groupIntoSections(resolved, UI_SCHEMA);
    expect(sections).toHaveLength(1);
    expect(sections[0].key).toBe("main");
    expect(sections[0].label).toBe("Widget Parameters");
    expect(sections[0].fields.map((f) => f.key)).toEqual([
      "intensity",
      "mode",
      "enabled",
    ]);

    // Basic-level sections remain visible under the "basic" filter.
    expect(filterByLevel(sections, false)).toHaveLength(1);
  });

  it("validates fixture fields through the shared validateField helper", () => {
    const resolved = resolveFields(SCHEMA, UI_SCHEMA);
    const intensity = resolved.find((f) => f.key === "intensity")!;

    expect(validateField(intensity, 50)).toBeNull();
    expect(validateField(intensity, 200)?.message).toContain("100");
    expect(validateField(intensity, -5)?.message).toContain("0");
    expect(validateField(intensity, undefined)?.message).toMatch(/required/i);
  });

  it("round-trips a spec containing the fixture-only block through the draft model", () => {
    const spec = {
      // NB: the draft adapter doesn't special-case `future_widgets`
      // as a known category — anything under an unknown top-level
      // key survives via `unknownBlocks` rather than as a tracked
      // entity. That is exactly the extensibility guarantee US-016
      // is asserting.
      market: { type: "cfamm", num_assets: 2, fee_bps: 30 },
      future_widgets: {
        kind: TYPE,
        intensity: 42,
        mode: "beta",
        enabled: true,
        exotic_bespoke_knob: { nested: [1, 2, 3] },
      },
    };

    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    const back = draftToApiSpec(draft);
    expect(back).toEqual(spec);

    // The unknown top-level block is preserved on the draft even
    // though the draft adapter has no descriptor for it.
    expect(draft.unknownBlocks.future_widgets).toEqual(spec.future_widgets);
  });

  it("survives the raw-spec editor round trip (US-014) with the fixture block intact", () => {
    const spec = {
      market: { type: "cfamm", num_assets: 2, fee_bps: 30 },
      future_widgets: {
        kind: TYPE,
        intensity: 42,
        exotic_bespoke_knob: "keep me",
      },
    };
    const result = parseRawSpecText(JSON.stringify(spec), {
      contract: CONTRACT,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(draftToApiSpec(result.draft)).toEqual(spec);
  });
});
