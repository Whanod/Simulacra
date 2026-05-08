import { describe, expect, it } from "vitest";
import {
  DEFAULT_SECTION_KEY,
  applyChainIdiomLabels,
  classifyProperty,
  coerceInput,
  filterByLevel,
  groupIntoSections,
  hasAdvancedContent,
  listSchemaProperties,
  resolveFields,
  titleCase,
  validateField,
  type ChainIdiomLabels,
  type ResolvedField,
} from "./fields";
import type { EntityUiSchema, JsonSchema } from "@/lib/types/contract";

describe("titleCase", () => {
  it("converts snake_case and camelCase to Title Case", () => {
    expect(titleCase("trade_min")).toBe("Trade Min");
    expect(titleCase("initialPrice")).toBe("Initial Price");
    expect(titleCase("fee-bps")).toBe("Fee Bps");
  });
});

describe("classifyProperty", () => {
  it("classifies scalar string/number/integer/boolean", () => {
    expect(classifyProperty({ type: "string" })).toBe("text");
    expect(classifyProperty({ type: "number" })).toBe("number");
    expect(classifyProperty({ type: "integer" })).toBe("integer");
    expect(classifyProperty({ type: "boolean" })).toBe("boolean");
  });

  it("classifies enum regardless of declared type", () => {
    expect(classifyProperty({ type: "string", enum: ["a", "b"] })).toBe("enum");
    expect(classifyProperty({ enum: [1, 2, 3] })).toBe("enum");
  });

  it("falls through to unsupported for nested / array / union types", () => {
    expect(classifyProperty({ type: "object" })).toBe("unsupported");
    expect(classifyProperty({ type: "array" })).toBe("unsupported");
    expect(classifyProperty({ type: ["string", "number"] })).toBe("unsupported");
    expect(classifyProperty({ $ref: "#/defs/foo" })).toBe("unsupported");
  });
});

describe("listSchemaProperties", () => {
  it("returns [] for missing / non-object schemas", () => {
    expect(listSchemaProperties(undefined)).toEqual([]);
    expect(listSchemaProperties({})).toEqual([]);
    expect(listSchemaProperties({ type: "string" })).toEqual([]);
  });

  it("preserves insertion order", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: {
        b: { type: "number" },
        a: { type: "string" },
        c: { type: "boolean" },
      },
    };
    expect(listSchemaProperties(schema).map((p) => p.key)).toEqual([
      "b",
      "a",
      "c",
    ]);
  });
});

describe("resolveFields", () => {
  const schema: JsonSchema = {
    type: "object",
    required: ["drift"],
    properties: {
      drift: {
        type: "number",
        title: "Drift",
        description: "Annualized drift",
        default: 0.05,
        minimum: 0,
        maximum: 1,
      },
      strategy: {
        type: "string",
        enum: ["aggressive", "passive"],
      },
      enabled: { type: "boolean" },
      basket: { type: "array" },
    },
  };

  const uiSchema: EntityUiSchema = {
    fields: {
      drift: { label: "μ (drift)", helpText: "overrides description" },
      strategy: {
        enumLabels: { aggressive: "Aggressive", passive: "Passive" },
      },
    },
  };

  it("resolves scalar, enum, and unsupported fields with UI overrides", () => {
    const fields = resolveFields(schema, uiSchema);
    expect(fields).toHaveLength(4);

    const drift = fields[0];
    expect(drift.key).toBe("drift");
    expect(drift.kind).toBe("number");
    expect(drift.label).toBe("μ (drift)");
    expect(drift.helpText).toBe("overrides description");
    expect(drift.required).toBe(true);
    expect(drift.default).toBe(0.05);

    const strategy = fields[1];
    expect(strategy.kind).toBe("enum");
    expect(strategy.enumValues).toEqual(["aggressive", "passive"]);
    expect(strategy.enumLabels).toEqual({
      aggressive: "Aggressive",
      passive: "Passive",
    });

    const enabled = fields[2];
    expect(enabled.kind).toBe("boolean");
    expect(enabled.label).toBe("Enabled");

    const basket = fields[3];
    expect(basket.kind).toBe("unsupported");
  });

  it("synthesizes labels from schema title or property key when uiSchema is absent", () => {
    const fields = resolveFields(schema, undefined);
    expect(fields[0].label).toBe("Drift");
    expect(fields[0].helpText).toBe("Annualized drift");
    expect(fields[1].label).toBe("Strategy");
    expect(fields[2].label).toBe("Enabled");
  });
});

describe("coerceInput", () => {
  const numberField: ResolvedField = {
    key: "drift",
    kind: "number",
    label: "Drift",
    required: false,
    level: "basic",
    schema: { type: "number" },
  };
  const integerField: ResolvedField = {
    key: "n",
    kind: "integer",
    label: "N",
    required: false,
    level: "basic",
    schema: { type: "integer" },
  };
  const boolField: ResolvedField = {
    key: "enabled",
    kind: "boolean",
    label: "Enabled",
    required: false,
    level: "basic",
    schema: { type: "boolean" },
  };

  it("parses numeric strings into numbers", () => {
    expect(coerceInput(numberField, "0.25")).toBe(0.25);
    expect(coerceInput(integerField, "42")).toBe(42);
  });

  it("returns undefined for empty numeric input", () => {
    expect(coerceInput(numberField, "")).toBeUndefined();
    expect(coerceInput(numberField, "   ")).toBeUndefined();
  });

  it("returns the original input on non-numeric coercion so validator can flag it", () => {
    expect(coerceInput(numberField, "abc")).toBe("abc");
  });

  it("parses string booleans", () => {
    expect(coerceInput(boolField, "true")).toBe(true);
    expect(coerceInput(boolField, "false")).toBe(false);
  });

  it("matches enum string input against declared numeric values", () => {
    const field: ResolvedField = {
      key: "tier",
      kind: "enum",
      label: "Tier",
      required: false,
      level: "basic",
      schema: { enum: [1, 2, 3] },
      enumValues: [1, 2, 3],
      enumLabels: { "1": "One", "2": "Two", "3": "Three" },
    };
    expect(coerceInput(field, "2")).toBe(2);
  });
});

describe("validateField", () => {
  const field: ResolvedField = {
    key: "drift",
    kind: "number",
    label: "Drift",
    required: true,
    level: "basic",
    schema: { type: "number", minimum: 0, maximum: 1 },
  };

  it("flags required-empty", () => {
    expect(validateField(field, undefined)?.message).toMatch(/required/i);
    expect(validateField(field, "")?.message).toMatch(/required/i);
  });

  it("flags wrong type", () => {
    expect(validateField(field, "abc")?.message).toMatch(/must be a number/);
  });

  it("flags out-of-range", () => {
    expect(validateField(field, -0.1)?.message).toMatch(/≥ 0/);
    expect(validateField(field, 1.5)?.message).toMatch(/≤ 1/);
  });

  it("passes in-range numeric values", () => {
    expect(validateField(field, 0.5)).toBeNull();
  });

  it("flags non-integer in integer field", () => {
    const int: ResolvedField = {
      key: "n",
      kind: "integer",
      label: "N",
      required: false,
      level: "basic",
      schema: { type: "integer" },
    };
    expect(validateField(int, 1.5)?.message).toMatch(/integer/);
    expect(validateField(int, 2)).toBeNull();
  });

  it("allows optional empty values", () => {
    const optional: ResolvedField = { ...field, required: false };
    expect(validateField(optional, undefined)).toBeNull();
  });

  it("flags enum values outside the declared set", () => {
    const enumField: ResolvedField = {
      key: "mode",
      kind: "enum",
      label: "Mode",
      required: false,
      level: "basic",
      schema: { enum: ["a", "b"] },
      enumValues: ["a", "b"],
      enumLabels: { a: "A", b: "B" },
    };
    expect(validateField(enumField, "c")?.message).toMatch(/one of/);
    expect(validateField(enumField, "a")).toBeNull();
  });
});

// ── US-009: ordering, widgets, sections, basic/advanced ──────────

describe("resolveFields ordering and metadata (US-009)", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      trade_max: { type: "number" },
      trade_min: { type: "number" },
      collateral: { type: "string" },
      frequency: { type: "number" },
    },
  };
  const uiSchema: EntityUiSchema = {
    fields: {
      trade_min: { order: 1, widget: "number", min: 0, section: "trade" },
      trade_max: { order: 2, widget: "number", min: 0, section: "trade" },
      frequency: {
        order: 1,
        widget: "slider",
        min: 0,
        max: 1,
        step: 0.01,
        section: "behavior",
      },
      collateral: { widget: "text", level: "advanced", section: "advanced" },
    },
  };

  it("orders by uiMeta.order with undefined sorting last and declaration as tiebreaker", () => {
    const fields = resolveFields(schema, uiSchema);
    // `collateral` has no order and sorts after ordered fields.
    expect(fields.map((f) => f.key)).toEqual([
      "trade_min",
      "frequency",
      "trade_max",
      "collateral",
    ]);
  });

  it("carries widget, section, level, and unit through from uiSchema", () => {
    const fields = resolveFields(schema, uiSchema);
    const freq = fields.find((f) => f.key === "frequency")!;
    expect(freq.widget).toBe("slider");
    expect(freq.section).toBe("behavior");
    expect(freq.level).toBe("basic");
    const col = fields.find((f) => f.key === "collateral")!;
    expect(col.level).toBe("advanced");
  });
});

describe("groupIntoSections (US-009)", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      trade_max: { type: "number" },
      trade_min: { type: "number" },
      frequency: { type: "number" },
      collateral: { type: "string" },
    },
  };
  const uiSchema: EntityUiSchema = {
    sections: [
      { key: "trade", label: "Trade Sizing", fields: ["trade_min", "trade_max"] },
      { key: "behavior", label: "Behavior", fields: ["frequency"] },
      {
        key: "advanced",
        label: "Advanced",
        level: "advanced",
        fields: ["collateral"],
      },
    ],
    fields: {
      trade_min: { order: 1 },
      trade_max: { order: 2 },
      frequency: { order: 1 },
      collateral: { widget: "text", level: "advanced", section: "advanced" },
    },
  };

  it("places fields into declared sections and promotes advanced level", () => {
    const fields = resolveFields(schema, uiSchema);
    const sections = groupIntoSections(fields, uiSchema);
    expect(sections.map((s) => s.key)).toEqual([
      "trade",
      "behavior",
      "advanced",
    ]);
    expect(sections[0].fields.map((f) => f.key)).toEqual([
      "trade_min",
      "trade_max",
    ]);
    expect(sections[1].fields.map((f) => f.key)).toEqual(["frequency"]);
    const advanced = sections[2];
    expect(advanced.level).toBe("advanced");
    expect(advanced.fields[0].level).toBe("advanced");
  });

  it("collects uncategorized fields into an 'Other' bucket", () => {
    const partial: EntityUiSchema = {
      sections: [{ key: "trade", label: "Trade Sizing", fields: ["trade_min"] }],
      fields: { trade_min: { order: 1 } },
    };
    const fields = resolveFields(schema, partial);
    const sections = groupIntoSections(fields, partial);
    expect(sections).toHaveLength(2);
    expect(sections[0].key).toBe("trade");
    expect(sections[1].key).toBe(DEFAULT_SECTION_KEY);
    expect(sections[1].fields.map((f) => f.key).sort()).toEqual(
      ["collateral", "frequency", "trade_max"].sort(),
    );
  });

  it("returns a single default section when uiSchema.sections is empty", () => {
    const sections = groupIntoSections(
      resolveFields(schema, undefined),
      undefined,
    );
    expect(sections).toHaveLength(1);
    expect(sections[0].key).toBe(DEFAULT_SECTION_KEY);
    expect(sections[0].label).toBe("");
  });
});

describe("applyChainIdiomLabels (Phase 0.1)", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      block_time: { type: "number" },
      epoch_length: { type: "integer" },
      drift: { type: "number" },
    },
  };

  it("applies_chain_idiom_labels_without_hooks", () => {
    const idiom: ChainIdiomLabels = {
      time_label: "Slot time",
      epoch_label: "Epoch (slots)",
      fee_label: "Compute & priority fees",
    };
    const fields = resolveFields(schema, undefined);
    const transformed = applyChainIdiomLabels(fields, idiom);
    const byKey = Object.fromEntries(transformed.map((f) => [f.key, f.label]));
    expect(byKey.block_time).toBe("Slot time");
    expect(byKey.epoch_length).toBe("Epoch (slots)");
    // Non-chain-keyed fields keep their synthesized label.
    expect(byKey.drift).toBe("Drift");
    // Wire-format keys must stay stable.
    expect(transformed.map((f) => f.key)).toEqual([
      "block_time",
      "epoch_length",
      "drift",
    ]);
  });

  it("falls back to original labels when idiom is undefined or slot missing", () => {
    const fields = resolveFields(schema, undefined);
    expect(applyChainIdiomLabels(fields, undefined)).toBe(fields);
    const partial = applyChainIdiomLabels(fields, { time_label: "Slot time" });
    const byKey = Object.fromEntries(partial.map((f) => [f.key, f.label]));
    expect(byKey.block_time).toBe("Slot time");
    // No epoch_label provided → label stays as title-cased default.
    expect(byKey.epoch_length).toBe("Epoch Length");
  });
});

describe("filterByLevel and hasAdvancedContent (US-009)", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      a: { type: "string" },
      b: { type: "string" },
      c: { type: "string" },
    },
  };
  const uiSchema: EntityUiSchema = {
    sections: [
      { key: "basic", label: "Basic", fields: ["a"] },
      { key: "advanced", label: "Advanced", level: "advanced", fields: ["c"] },
    ],
    fields: {
      a: {},
      b: { level: "advanced" }, // unclaimed → goes to Other
      c: {},
    },
  };

  it("hides advanced fields and advanced sections in basic mode", () => {
    const fields = resolveFields(schema, uiSchema);
    const grouped = groupIntoSections(fields, uiSchema);
    expect(hasAdvancedContent(grouped)).toBe(true);

    const basicOnly = filterByLevel(grouped, false);
    // `advanced` section dropped, `b` dropped from "Other", only `basic` remains.
    expect(basicOnly.map((s) => s.key)).toEqual(["basic"]);
    expect(basicOnly[0].fields.map((f) => f.key)).toEqual(["a"]);
  });

  it("shows everything when showAdvanced is true", () => {
    const fields = resolveFields(schema, uiSchema);
    const grouped = groupIntoSections(fields, uiSchema);
    const all = filterByLevel(grouped, true);
    expect(all.length).toBe(grouped.length);
  });

  it("hasAdvancedContent returns false when nothing is advanced", () => {
    const plain: EntityUiSchema = {
      sections: [{ key: "s", label: "S", fields: ["a"] }],
      fields: { a: {} },
    };
    const fields = resolveFields(
      { type: "object", properties: { a: { type: "string" } } },
      plain,
    );
    const grouped = groupIntoSections(fields, plain);
    expect(hasAdvancedContent(grouped)).toBe(false);
  });
});
