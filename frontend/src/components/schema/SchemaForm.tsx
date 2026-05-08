"use client";

/**
 * Generic schema field renderer (US-008 + US-009).
 *
 * Renders an editable form for a `DraftEntity` from its
 * backend-provided JSON Schema + UI metadata.
 *
 * US-008 scope (scalar core):
 *   - text / number / integer / boolean
 *   - enum with backend-provided labels
 *   - validation errors from `validateField`
 *   - unsupported field structures fall back to raw-JSON editing
 *     via `RawJsonEditor` rather than crashing or being dropped
 *
 * US-009 additions (UI metadata):
 *   - field ordering via `uiMeta.order`
 *   - section grouping via `uiSchema.sections`
 *   - widget hints: slider / select / switch / textarea /
 *     code-editor / token-list / json (plus the scalar widgets)
 *   - basic / advanced toggle — the form starts in "basic" view
 *     whenever any field or section is advanced-only, and exposes a
 *     toggle button to reveal the rest
 *
 * When the entity has no `schema` at all, the whole form renders as
 * a raw-JSON editor over `entity.params`.
 */

import { useMemo, useState } from "react";
import type { DraftEntity } from "@/lib/types/drafts";
import {
  applyChainIdiomLabels,
  coerceInput,
  filterByLevel,
  groupIntoSections,
  hasAdvancedContent,
  resolveFields,
  validateField,
  type ChainIdiomLabels,
  type ResolvedField,
  type ResolvedSection,
} from "@/lib/schema/fields";
import { RawJsonEditor } from "./RawJsonEditor";
import { getSpecialEditor } from "./specialEditors";

interface SchemaFormProps {
  entity: DraftEntity;
  onChange: (params: Record<string, unknown>) => void;
  /**
   * Optional test-only opt-out of the form-wrapper tag so the form
   * can nest inside a parent form. Defaults to `true`.
   */
  standalone?: boolean;
  /**
   * Chain-idiom display labels applied to wire-format-stable keys
   * (`block_time`, `epoch_length`, ...). The form layer reads this
   * from `useChainIdiom()` and passes it in; the resolver itself
   * stays pure and React-hook-free.
   */
  idiom?: ChainIdiomLabels;
}

export function SchemaForm({
  entity,
  onChange,
  standalone = true,
  idiom,
}: SchemaFormProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Fallback chain (US-010):
  //   1. uiSchema.specialEditor resolves to a registered plugin
  //   2. schema + resolved sections → generic renderer below
  //   3. raw JSON over the whole params block
  const SpecialEditor = getSpecialEditor(entity.uiSchema?.specialEditor);

  const sections = useMemo(() => {
    const fields = applyChainIdiomLabels(
      resolveFields(entity.schema, entity.uiSchema),
      idiom,
    );
    return groupIntoSections(fields, entity.uiSchema);
  }, [entity.schema, entity.uiSchema, idiom]);

  const hasAdvanced = useMemo(() => hasAdvancedContent(sections), [sections]);
  const visibleSections = useMemo(
    () => filterByLevel(sections, showAdvanced),
    [sections, showAdvanced],
  );

  if (SpecialEditor) {
    const pluginBody = (
      <SpecialEditor entity={entity} onChange={onChange} />
    );
    if (!standalone) return pluginBody;
    return (
      <form
        className="schema-form-root"
        data-special-editor-key={entity.uiSchema?.specialEditor}
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        {pluginBody}
      </form>
    );
  }

  if (!entity.schema || sections.length === 0 || allEmpty(sections)) {
    return (
      <RawJsonEditor
        label={entity.label}
        value={entity.params}
        onChange={(next) => {
          if (next && typeof next === "object" && !Array.isArray(next)) {
            onChange(next as Record<string, unknown>);
          } else {
            onChange({});
          }
        }}
      />
    );
  }

  const updateField = (key: string, nextValue: unknown) => {
    onChange({ ...entity.params, [key]: nextValue });
  };

  const body = (
    <div className="schema-form">
      {hasAdvanced ? (
        <div
          className="schema-form-toolbar"
          style={{
            display: "flex",
            justifyContent: "flex-end",
            marginBottom: 12,
          }}
        >
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            aria-pressed={showAdvanced}
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "Hide advanced" : "Show advanced"}
          </button>
        </div>
      ) : null}

      {visibleSections.map((section) => (
        <SectionBlock
          key={section.key}
          section={section}
          params={entity.params}
          onChangeField={updateField}
        />
      ))}
    </div>
  );

  if (!standalone) return body;
  return (
    <form
      className="schema-form-root"
      onSubmit={(e) => {
        e.preventDefault();
      }}
    >
      {body}
    </form>
  );
}

function allEmpty(sections: ResolvedSection[]): boolean {
  return sections.every((s) => s.fields.length === 0);
}

interface SectionBlockProps {
  section: ResolvedSection;
  params: Record<string, unknown>;
  onChangeField: (key: string, next: unknown) => void;
}

function SectionBlock({ section, params, onChangeField }: SectionBlockProps) {
  return (
    <div
      className="form-section"
      data-section-key={section.key}
      data-level={section.level}
    >
      {section.label ? (
        <h4>
          {section.label}
          {section.level === "advanced" ? (
            <span className="hint" style={{ marginLeft: 8 }}>
              advanced
            </span>
          ) : null}
        </h4>
      ) : null}
      {section.description ? (
        <p className="hint" style={{ marginTop: -8, marginBottom: 12 }}>
          {section.description}
        </p>
      ) : null}
      {section.fields.map((field) => (
        <FieldRow
          key={field.key}
          field={field}
          value={params[field.key] ?? field.default}
          onChange={(next) => onChangeField(field.key, next)}
          fallback={
            <RawJsonEditor
              label={field.label}
              value={params[field.key]}
              onChange={(next) => onChangeField(field.key, next)}
              compact
            />
          }
        />
      ))}
    </div>
  );
}

interface FieldRowProps {
  field: ResolvedField;
  value: unknown;
  onChange: (next: unknown) => void;
  fallback: React.ReactNode;
}

function FieldRow({ field, value, onChange, fallback }: FieldRowProps) {
  // Only fall back when the schema classification is unsupported AND
  // the widget hint does not rescue the field. Explicit widgets like
  // "number" / "text" / "slider" on a union-typed schema property
  // (e.g. `max_trade: anyOf[integer, number]`) are still renderable.
  if (field.kind === "unsupported" && !hasRenderableWidget(field)) {
    return <div className="form-group">{fallback}</div>;
  }

  const error = validateField(field, value);

  return (
    <div
      className="form-group"
      data-field-key={field.key}
      data-level={field.level}
    >
      <label htmlFor={inputId(field)}>
        {field.label}
        {field.required ? <span aria-label="required"> *</span> : null}
        {field.unit ? (
          <span className="hint" style={{ marginLeft: 6 }}>
            ({field.unit})
          </span>
        ) : null}
      </label>
      <FieldInput field={field} value={value} onChange={onChange} fallback={fallback} />
      {field.helpText ? <p className="hint">{field.helpText}</p> : null}
      {error ? (
        <p className="hint" style={{ color: "var(--red)" }} role="alert">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}

function hasRenderableWidget(field: ResolvedField): boolean {
  if (!field.widget) return false;
  const renderable = new Set([
    "slider",
    "switch",
    "textarea",
    "code-editor",
    "token-list",
    "json",
    "select",
    "text",
    "number",
    "integer",
    "boolean",
  ]);
  return renderable.has(field.widget);
}

function inputId(field: ResolvedField): string {
  return `schema-field-${field.key}`;
}

interface FieldInputProps {
  field: ResolvedField;
  value: unknown;
  onChange: (next: unknown) => void;
  fallback: React.ReactNode;
}

function FieldInput({ field, value, onChange, fallback }: FieldInputProps) {
  const id = inputId(field);
  const widget = resolveWidget(field);

  switch (widget) {
    case "slider": {
      const min = numericOr(field.uiMeta?.min, field.schema.minimum, 0);
      const max = numericOr(field.uiMeta?.max, field.schema.maximum, 1);
      const step = numericOr(field.uiMeta?.step, undefined, 0.01);
      const num =
        typeof value === "number"
          ? value
          : typeof value === "string" && value !== ""
            ? Number.parseFloat(value)
            : min;
      const displayed = Number.isFinite(num) ? num : min;
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <input
            id={id}
            type="range"
            min={min}
            max={max}
            step={step}
            value={displayed}
            onChange={(e) => onChange(Number.parseFloat(e.target.value))}
            style={{ flex: 1 }}
          />
          <span
            className="mono"
            style={{ fontFamily: "var(--font-mono)", fontSize: ".8rem" }}
          >
            {displayed}
          </span>
        </div>
      );
    }
    case "switch": {
      return (
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
      );
    }
    case "textarea": {
      return (
        <textarea
          id={id}
          rows={4}
          value={typeof value === "string" ? value : value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    }
    case "code-editor": {
      return (
        <textarea
          id={id}
          rows={6}
          spellCheck={false}
          style={{ fontFamily: "var(--font-mono)", fontSize: ".8rem" }}
          value={typeof value === "string" ? value : value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    }
    case "token-list": {
      // Edit as comma-separated tokens; commit as string[]. Accepts
      // incoming string[] or string and always yields string[].
      const asArray = Array.isArray(value)
        ? value.map((v) => String(v))
        : typeof value === "string" && value.length > 0
          ? value.split(",").map((t) => t.trim())
          : [];
      return (
        <input
          id={id}
          type="text"
          placeholder="comma,separated,tokens"
          value={asArray.join(", ")}
          onChange={(e) => {
            const next = e.target.value
              .split(",")
              .map((t) => t.trim())
              .filter((t) => t.length > 0);
            onChange(next);
          }}
        />
      );
    }
    case "json": {
      return <>{fallback}</>;
    }
    case "select": {
      // Explicit select widget; only meaningful when enum values
      // are declared. Falls through to text if none.
      if (field.enumValues && field.enumValues.length > 0) {
        return renderEnumSelect(id, field, value, onChange);
      }
      return renderTextInput(id, field, value, onChange);
    }
    case "text": {
      return renderTextInput(id, field, value, onChange);
    }
    case "number":
    case "integer": {
      return (
        <input
          id={id}
          type="number"
          step={widget === "integer" ? 1 : "any"}
          min={toNumberAttr(field.uiMeta?.min ?? field.schema.minimum)}
          max={toNumberAttr(field.uiMeta?.max ?? field.schema.maximum)}
          value={
            typeof value === "number"
              ? value
              : typeof value === "string"
                ? value
                : ""
          }
          onChange={(e) => onChange(coerceInput(field, e.target.value))}
        />
      );
    }
    case "boolean": {
      return (
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
      );
    }
    case "enum": {
      return renderEnumSelect(id, field, value, onChange);
    }
    default:
      return <>{fallback}</>;
  }
}

/**
 * Resolves the widget to render. Explicit `uiMeta.widget` wins; if
 * that is absent or names an unknown widget, fall back to the kind
 * the schema classification produced in US-008.
 */
function resolveWidget(field: ResolvedField): string {
  const explicit = field.widget;
  const recognized = new Set([
    "slider",
    "switch",
    "textarea",
    "code-editor",
    "token-list",
    "json",
    "select",
    "text",
    "number",
    "integer",
    "boolean",
    "enum",
  ]);
  if (explicit && recognized.has(explicit)) return explicit;
  return field.kind;
}

function renderTextInput(
  id: string,
  field: ResolvedField,
  value: unknown,
  onChange: (next: unknown) => void,
): React.ReactElement {
  return (
    <input
      id={id}
      type="text"
      value={typeof value === "string" ? value : value == null ? "" : String(value)}
      onChange={(e) => onChange(coerceInput(field, e.target.value))}
    />
  );
}

function renderEnumSelect(
  id: string,
  field: ResolvedField,
  value: unknown,
  onChange: (next: unknown) => void,
): React.ReactElement {
  const values = field.enumValues ?? [];
  const labels = field.enumLabels ?? {};
  return (
    <select
      id={id}
      value={value == null ? "" : String(value)}
      onChange={(e) => onChange(coerceInput(field, e.target.value))}
    >
      {values.map((v) => {
        const keyStr = String(v);
        return (
          <option key={keyStr} value={keyStr}>
            {labels[keyStr] ?? keyStr}
          </option>
        );
      })}
    </select>
  );
}

function numericOr(
  primary: unknown,
  secondary: unknown,
  fallback: number,
): number {
  if (typeof primary === "number") return primary;
  if (typeof secondary === "number") return secondary;
  return fallback;
}

function toNumberAttr(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}
