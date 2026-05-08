/**
 * Schema-driven registry contract types (US-007).
 *
 * These mirror the backend contract described in refactor.md
 * (Target Architecture §1 and §6). The backend is the source of
 * truth for this contract; the frontend consumes it through the
 * registry service and renders entities generically.
 *
 * Partial-metadata behavior:
 * - `label` is the only required field on a definition; every other
 *   enrichment field (`description`, `schema`, `uiSchema`, `defaults`,
 *   `badges`, `examples`, `metadata`) is optional.
 * - When `schema` is missing, the generic renderer falls back to
 *   raw-JSON editing (US-014) rather than crashing.
 * - When `uiSchema` is missing, fields render in insertion order
 *   with title-cased labels and no sectioning.
 * - When `uiSchema.fields[key]` is missing for a schema property,
 *   the renderer synthesizes defaults (label = title-cased key,
 *   widget inferred from the JSON Schema type).
 * - When `uiSchema.sections` is missing, all fields render in a
 *   single implicit section.
 * - When `defaults` is missing, new entities start with an empty
 *   params object and rely on the backend to fill in defaults at
 *   build time.
 * - When `builderSupported` is false, the entity renders read-only
 *   with a clear "unsupported" affordance and still round-trips
 *   through the generic draft model without loss.
 *
 * `colorHint` has a single owner per level:
 * - `RegistryEntityDefinition.colorHint` — entity card / badge /
 *   legend swatch for the whole entity.
 * - `UiFieldMeta.colorHint` — per-field swatch (e.g. per-enum value
 *   in a select). Field-level color never inherits from the entity.
 */

/**
 * A JSON Schema document as delivered by the backend. Intentionally
 * typed as a loose record: the renderer walks the tree and handles
 * unknown keywords gracefully.
 */
export type JsonSchema = Record<string, unknown>;

export interface RegistryBadge {
  label: string;
  variant?: string;
}

export type UiWidget =
  | "text"
  | "number"
  | "slider"
  | "select"
  | "switch"
  | "textarea"
  | "code-editor"
  | "token-list"
  | "json"
  | (string & {});

export type UiLevel = "basic" | "advanced";

export interface UiFieldMeta {
  label?: string;
  helpText?: string;
  placeholder?: string;
  order?: number;
  section?: string;
  level?: UiLevel;
  widget?: UiWidget;
  enumLabels?: Record<string, string>;
  colorHint?: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}

export interface UiSection {
  key: string;
  label: string;
  description?: string;
  order?: number;
  level?: UiLevel;
  fields: string[];
}

export type SpecialEditorKey =
  | "world-markets-graph"
  | "code-editor"
  | (string & {});

export interface EntityUiSchema {
  sections?: UiSection[];
  fields?: Record<string, UiFieldMeta>;
  specialEditor?: SpecialEditorKey;
}

export interface RegistryEntityDefinition {
  category: string;
  type: string;
  label: string;
  description?: string;
  badges?: RegistryBadge[];
  colorHint?: string;
  builderSupported: boolean;
  schema?: JsonSchema;
  uiSchema?: EntityUiSchema;
  defaults?: Record<string, unknown>;
  examples?: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
}

export interface RegistryCategoryDefinition {
  key: string;
  label: string;
  description?: string;
  order?: number;
  entities: RegistryEntityDefinition[];
}

/**
 * Top-level contract response. `contractVersion` lets the frontend
 * degrade gracefully when the backend ships a newer shape: unknown
 * versions fall back to the raw-JSON renderer for any field that
 * would otherwise require new schema support.
 */
export interface RegistryContractResponse {
  contractVersion: string;
  categories: RegistryCategoryDefinition[];
}

export const SUPPORTED_CONTRACT_VERSION = "v2";

export function isSupportedContractVersion(version: string): boolean {
  return version === SUPPORTED_CONTRACT_VERSION;
}
