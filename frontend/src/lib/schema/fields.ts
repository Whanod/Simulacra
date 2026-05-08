/**
 * Pure helpers for the generic schema field renderer (US-008).
 *
 * These functions walk a backend-provided JSON Schema and produce a
 * list of `ResolvedField` descriptors that the React renderer can map
 * straight to inputs. Everything here is intentionally side-effect
 * free so it can be unit-tested without mounting React.
 *
 * Scope (US-008):
 *   - scalar text / number / integer / boolean
 *   - enum with backend-provided labels
 *   - anything else falls through to `kind: "unsupported"`, which the
 *     renderer shows as a raw-JSON editor
 *
 * US-009 extends this with widget hints, ordering, sections, and
 * basic/advanced presentation. The resolver already reads `uiSchema`
 * to the minimum extent needed so US-009 is additive, not a rewrite.
 */

import type {
  EntityUiSchema,
  JsonSchema,
  UiFieldMeta,
} from "@/lib/types/contract";

export type ScalarKind = "text" | "number" | "integer" | "boolean" | "enum";
export type FieldKind = ScalarKind | "unsupported";

/** Section level from `UiSection.level` / `UiFieldMeta.level`. */
export type LevelFilter = "basic" | "advanced";

export interface ResolvedField {
  key: string;
  kind: FieldKind;
  label: string;
  helpText?: string;
  required: boolean;
  /** Raw JSON Schema node for this property — used by the fallback. */
  schema: JsonSchema;
  /** Hand-authored UI metadata for this property, if any. */
  uiMeta?: UiFieldMeta;
  /** Backend-declared default, if any. */
  default?: unknown;
  /** Enum values (only present when kind === "enum"). */
  enumValues?: ReadonlyArray<string | number>;
  /** Enum display labels keyed by value (strings keyed by String(value)). */
  enumLabels?: Record<string, string>;
  /**
   * Explicit widget hint from `uiSchema.fields[key].widget`. When
   * present this overrides the default widget inferred from `kind`
   * (e.g. `widget: "slider"` on a numeric field renders a range
   * input instead of a plain number input). US-008 ignored this;
   * US-009 honors it.
   */
  widget?: string;
  /** Section key this field belongs to, if any. */
  section?: string;
  /**
   * Basic/advanced classification. A field is advanced when either
   * its own `uiMeta.level` is `"advanced"` or the section it lives
   * in is `"advanced"`. Defaults to `"basic"`.
   */
  level: LevelFilter;
  /** Render order within a section. Undefined sorts after numbered. */
  order?: number;
  /** Unit suffix to show next to the input (e.g. `"gwei"`, `"bps"`). */
  unit?: string;
}

export interface ResolvedSection {
  key: string;
  label: string;
  description?: string;
  level: LevelFilter;
  fields: ResolvedField[];
}

/** Key of the implicit "uncategorized" section used as a fallback. */
export const DEFAULT_SECTION_KEY = "__default";

export interface ValidationError {
  message: string;
}

/** Returns true for plain `{key: value}` object literals. */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Title-cases a schema property key when no hand-authored label is
 * provided. `trade_min` -> `Trade Min`, `initialPrice` -> `Initial Price`.
 */
export function titleCase(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b([a-z])/g, (_, c: string) => c.toUpperCase())
    .trim();
}

/**
 * Given a JSON Schema property node, classify it for the renderer.
 * Returns `"unsupported"` for anything this pass does not handle yet
 * (nested objects, arrays, oneOf, $ref, etc.). The caller uses the
 * result to route unsupported nodes to raw-JSON editing.
 */
export function classifyProperty(prop: JsonSchema): FieldKind {
  if (!isPlainObject(prop)) return "unsupported";

  // `enum` short-circuits type — a property with enum is always an enum
  // widget, regardless of declared `type`.
  if (Array.isArray(prop.enum) && prop.enum.length > 0) {
    return "enum";
  }

  // JSON Schema `type` can be a string or an array; we only handle the
  // single-type case here. Union types fall through to unsupported.
  const type = prop.type;
  if (typeof type !== "string") return "unsupported";

  switch (type) {
    case "string":
      return "text";
    case "number":
      return "number";
    case "integer":
      return "integer";
    case "boolean":
      return "boolean";
    default:
      return "unsupported";
  }
}

/**
 * Extracts the list of properties from a JSON Schema's top-level
 * `properties` map in insertion order. Non-`object` schemas and
 * schemas with no `properties` key yield an empty list.
 */
export function listSchemaProperties(
  schema: JsonSchema | undefined,
): Array<{ key: string; prop: JsonSchema }> {
  if (!schema || !isPlainObject(schema)) return [];
  const props = schema.properties;
  if (!isPlainObject(props)) return [];
  const out: Array<{ key: string; prop: JsonSchema }> = [];
  for (const [key, value] of Object.entries(props)) {
    if (isPlainObject(value)) {
      out.push({ key, prop: value });
    }
  }
  return out;
}

function isRequired(schema: JsonSchema | undefined, key: string): boolean {
  if (!schema || !isPlainObject(schema)) return false;
  const required = schema.required;
  return Array.isArray(required) && required.includes(key);
}

function enumValuesFrom(prop: JsonSchema): ReadonlyArray<string | number> {
  const raw = prop.enum;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (v): v is string | number => typeof v === "string" || typeof v === "number",
  );
}

/**
 * Resolves a JSON Schema + UI schema into an ordered list of
 * renderable fields. Ordering:
 *   1. `uiSchema.fields[key].order` ascending (numeric; undefined sorts
 *      after numbered entries)
 *   2. original JSON Schema declaration order as the stable tiebreaker
 *
 * Grouping into sections and basic/advanced filtering are applied by
 * `groupIntoSections` / `filterByLevel` so the raw ordered list stays
 * reusable for callers that want their own layout.
 */
export function resolveFields(
  schema: JsonSchema | undefined,
  uiSchema: EntityUiSchema | undefined,
): ResolvedField[] {
  const properties = listSchemaProperties(schema);
  const fieldMeta = uiSchema?.fields ?? {};

  const resolved = properties.map<ResolvedField>(({ key, prop }, index) => {
    const kind = classifyProperty(prop);
    const uiMeta = fieldMeta[key];
    const label =
      uiMeta?.label ??
      (typeof prop.title === "string" ? prop.title : undefined) ??
      titleCase(key);
    const helpText =
      uiMeta?.helpText ??
      (typeof prop.description === "string" ? prop.description : undefined);

    const field: ResolvedField = {
      key,
      kind,
      label,
      helpText,
      required: isRequired(schema, key),
      schema: prop,
      uiMeta,
      default: prop.default,
      widget: uiMeta?.widget,
      section: uiMeta?.section,
      level: uiMeta?.level === "advanced" ? "advanced" : "basic",
      order: uiMeta?.order,
      unit: uiMeta?.unit,
    };

    if (kind === "enum") {
      const values = enumValuesFrom(prop);
      field.enumValues = values;
      const labels: Record<string, string> = {};
      for (const v of values) {
        const keyStr = String(v);
        labels[keyStr] = uiMeta?.enumLabels?.[keyStr] ?? keyStr;
      }
      field.enumLabels = labels;
    }

    // Stash the original declaration index on the object so the sort
    // below can use it as a stable tiebreaker without mutating the
    // public shape.
    (field as ResolvedField & { __index: number }).__index = index;
    return field;
  });

  resolved.sort((a, b) => {
    const ao = a.order;
    const bo = b.order;
    if (ao !== undefined && bo !== undefined && ao !== bo) return ao - bo;
    if (ao !== undefined && bo === undefined) return -1;
    if (ao === undefined && bo !== undefined) return 1;
    return (
      (a as ResolvedField & { __index: number }).__index -
      (b as ResolvedField & { __index: number }).__index
    );
  });

  // Strip the private tiebreaker index before returning.
  return resolved.map((f) => {
    const { __index: _drop, ...rest } = f as ResolvedField & {
      __index: number;
    };
    void _drop;
    return rest;
  });
}

/**
 * Groups a list of resolved fields into sections following the
 * `uiSchema.sections` declaration. Rules:
 *   - If `uiSchema.sections` is defined, render one `ResolvedSection`
 *     per declared section in order. A field lands in a section when
 *     its `uiMeta.section` matches the section key OR when the
 *     section's `fields[]` list contains the field's key.
 *   - Any field with no matching section falls into an implicit
 *     `DEFAULT_SECTION_KEY` bucket rendered after the declared ones,
 *     labeled "Other". This keeps unsupported/partial metadata from
 *     being dropped.
 *   - If `uiSchema.sections` is undefined or empty, everything lands
 *     in a single `DEFAULT_SECTION_KEY` section with no label.
 *   - A section inherits `advanced` when its `level` is `"advanced"`;
 *     fields inside such sections are treated as advanced too so
 *     basic/advanced toggling works as a single switch.
 */
export function groupIntoSections(
  fields: ResolvedField[],
  uiSchema: EntityUiSchema | undefined,
): ResolvedSection[] {
  const declared = uiSchema?.sections ?? [];
  if (declared.length === 0) {
    return [
      {
        key: DEFAULT_SECTION_KEY,
        label: "",
        level: "basic",
        fields,
      },
    ];
  }

  const byKey = new Map<string, ResolvedField>();
  for (const f of fields) byKey.set(f.key, f);
  const unclaimed = new Set(fields.map((f) => f.key));

  const sections: ResolvedSection[] = declared.map((section) => {
    const sectionLevel: LevelFilter =
      section.level === "advanced" ? "advanced" : "basic";
    const matched: ResolvedField[] = [];
    // Explicit fields[] list on the section wins first.
    for (const fieldKey of section.fields) {
      const field = byKey.get(fieldKey);
      if (field) {
        matched.push(
          sectionLevel === "advanced" && field.level === "basic"
            ? { ...field, level: "advanced" }
            : field,
        );
        unclaimed.delete(fieldKey);
      }
    }
    // Fields that point at this section via uiMeta.section but were
    // not listed in fields[] still land here.
    for (const f of fields) {
      if (!unclaimed.has(f.key)) continue;
      if (f.section === section.key) {
        matched.push(
          sectionLevel === "advanced" && f.level === "basic"
            ? { ...f, level: "advanced" }
            : f,
        );
        unclaimed.delete(f.key);
      }
    }
    return {
      key: section.key,
      label: section.label,
      description: section.description,
      level: sectionLevel,
      fields: matched,
    };
  });

  const leftovers = fields.filter((f) => unclaimed.has(f.key));
  if (leftovers.length > 0) {
    sections.push({
      key: DEFAULT_SECTION_KEY,
      label: "Other",
      level: "basic",
      fields: leftovers,
    });
  }

  return sections;
}

/**
 * Filters resolved sections by visibility level. When
 * `showAdvanced` is false, advanced fields and fully-advanced
 * sections are dropped; when true, everything renders. A section
 * that ends up empty after filtering is removed so the form does
 * not show empty headers.
 */
export function filterByLevel(
  sections: ResolvedSection[],
  showAdvanced: boolean,
): ResolvedSection[] {
  if (showAdvanced) return sections;
  const out: ResolvedSection[] = [];
  for (const section of sections) {
    if (section.level === "advanced") continue;
    const visibleFields = section.fields.filter((f) => f.level !== "advanced");
    if (visibleFields.length === 0) continue;
    out.push({ ...section, fields: visibleFields });
  }
  return out;
}

/**
 * Pure label-transform context for chain-idiom-aware display labels.
 *
 * The schema's wire-format field keys (`block_time`, `epoch_length`)
 * stay stable; only the display label changes. The React form layer
 * constructs this context (typically from `useChainIdiom()`) and
 * passes it in — this module never imports React hooks so it stays
 * unit-testable without mounting React.
 */
export interface ChainIdiomLabels {
  /** Replacement label for the `block_time` wire-format field. */
  time_label?: string;
  /** Replacement label for the `epoch_length` wire-format field. */
  epoch_label?: string;
  /** Replacement label for fee-model fields (`gas`, `fee_model`). */
  fee_label?: string;
}

/**
 * Maps stable wire-format field keys to the idiom slot whose label
 * replaces the synthesized one. Keys absent from this map are left
 * untouched.
 */
const CHAIN_IDIOM_KEY_MAP: Record<string, keyof ChainIdiomLabels> = {
  block_time: "time_label",
  epoch_length: "epoch_label",
  gas: "fee_label",
  fee_model: "fee_label",
};

/**
 * Returns a copy of `fields` with chain-idiom-aware display labels
 * substituted on the well-known wire-format keys above. Pure: no
 * React hooks. Pass `undefined` to leave labels unchanged.
 */
export function applyChainIdiomLabels(
  fields: ResolvedField[],
  idiom: ChainIdiomLabels | undefined,
): ResolvedField[] {
  if (!idiom) return fields;
  return fields.map((f) => {
    const slot = CHAIN_IDIOM_KEY_MAP[f.key];
    if (!slot) return f;
    const replacement = idiom[slot];
    if (!replacement || f.label === replacement) return f;
    return { ...f, label: replacement };
  });
}

/** True when any section or field requires the advanced toggle. */
export function hasAdvancedContent(sections: ResolvedSection[]): boolean {
  for (const section of sections) {
    if (section.level === "advanced") return true;
    for (const f of section.fields) {
      if (f.level === "advanced") return true;
    }
  }
  return false;
}

/**
 * Coerces a raw HTML-input value to the typed value the schema
 * expects. Text inputs always arrive as strings; this function turns
 * them back into numbers / booleans when the field kind demands it.
 *
 * Returns the coerced value, or the original input unchanged if the
 * kind is `text`, `unsupported`, or if coercion would produce `NaN`
 * (in which case the renderer shows a validation error instead).
 */
export function coerceInput(field: ResolvedField, input: unknown): unknown {
  switch (field.kind) {
    case "number":
    case "integer": {
      if (typeof input === "number") return input;
      if (typeof input === "string") {
        if (input.trim() === "") return undefined;
        const parsed = field.kind === "integer"
          ? Number.parseInt(input, 10)
          : Number.parseFloat(input);
        return Number.isNaN(parsed) ? input : parsed;
      }
      return input;
    }
    case "boolean": {
      if (typeof input === "boolean") return input;
      if (input === "true") return true;
      if (input === "false") return false;
      return input;
    }
    case "enum": {
      // Enum values may be numeric; reconcile string input against
      // the declared value list.
      if (field.enumValues && typeof input === "string") {
        const match = field.enumValues.find((v) => String(v) === input);
        return match ?? input;
      }
      return input;
    }
    default:
      return input;
  }
}

/**
 * Returns a human-readable error for a value that fails the field's
 * basic constraints, or `null` if the value is acceptable. US-008
 * covers type checks, required, and numeric min/max. Deeper schema
 * validation (pattern, format, conditional) is deferred to the
 * backend — the renderer is advisory.
 */
export function validateField(
  field: ResolvedField,
  value: unknown,
): ValidationError | null {
  const isEmpty =
    value === undefined ||
    value === null ||
    (typeof value === "string" && value.trim() === "");

  if (isEmpty) {
    if (field.required) return { message: `${field.label} is required` };
    return null;
  }

  switch (field.kind) {
    case "text":
      if (typeof value !== "string") {
        return { message: `${field.label} must be text` };
      }
      return null;
    case "number":
    case "integer": {
      if (typeof value !== "number" || Number.isNaN(value)) {
        return { message: `${field.label} must be a number` };
      }
      if (field.kind === "integer" && !Number.isInteger(value)) {
        return { message: `${field.label} must be an integer` };
      }
      const min = numericConstraint(field, "minimum", "min");
      const max = numericConstraint(field, "maximum", "max");
      if (min !== undefined && value < min) {
        return { message: `${field.label} must be ≥ ${min}` };
      }
      if (max !== undefined && value > max) {
        return { message: `${field.label} must be ≤ ${max}` };
      }
      return null;
    }
    case "boolean":
      if (typeof value !== "boolean") {
        return { message: `${field.label} must be true or false` };
      }
      return null;
    case "enum": {
      if (!field.enumValues || field.enumValues.length === 0) return null;
      const match = field.enumValues.some((v) => v === value);
      if (!match) {
        return { message: `${field.label} must be one of the listed options` };
      }
      return null;
    }
    default:
      return null;
  }
}

function numericConstraint(
  field: ResolvedField,
  schemaKey: "minimum" | "maximum",
  uiKey: "min" | "max",
): number | undefined {
  const fromSchema = field.schema[schemaKey];
  if (typeof fromSchema === "number") return fromSchema;
  const fromUi = field.uiMeta?.[uiKey];
  if (typeof fromUi === "number") return fromUi;
  return undefined;
}
