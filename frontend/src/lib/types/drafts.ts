/**
 * Generic draft model (US-005).
 *
 * The draft is a schema-driven abstraction over a simulation spec.
 * It decouples editing from the fixed `RunSpec` shape so that
 * arbitrary backend-defined entities can be edited and round-tripped
 * without being coerced into frontend defaults.
 *
 * Merge rule (US-005 / US-006):
 * - `rawSpec` is the original spec captured at load time. It is not
 *   mutated by edits — it is the preservation anchor for unknown
 *   fields.
 * - Each `DraftEntity` has a `raw` snapshot of its source block and
 *   a `params` overlay of edited values.
 * - When serializing, the draft walks `rawSpec` by each entity's
 *   `configPath` and, per block, performs a key-by-key merge: for
 *   every key in `params`, overwrite the value at that key in the
 *   cloned block; keys that exist in `raw` but are absent from
 *   `params` are preserved verbatim.
 * - Any top-level keys in `rawSpec` that no entity touches are
 *   preserved verbatim via `unknownBlocks`.
 *
 * The practical consequence: `api spec -> draft -> api spec` is
 * no-loss for both supported (edited) and unsupported (untouched)
 * fields.
 */

import type { EntityUiSchema, JsonSchema } from "./contract";

export interface DraftEntity {
  /** Backend category identifier (e.g. "markets", "agents"). */
  category: string;
  /** Backend type identifier (e.g. "cfamm", "noise"). */
  type: string;
  /** Human-readable label shown in the UI. */
  label: string;
  /**
   * Dotted path (or JSON-pointer-style slash path) into `rawSpec`
   * that locates the block this entity edits. Examples:
   *   "market", "clock", "execution", "fee_model",
   *   "agents.role_params.noise", "feeds.0".
   */
  configPath: string;
  /** Edited values keyed by schema property name. */
  params: Record<string, unknown>;
  /** Snapshot of the original block at load time. */
  raw: Record<string, unknown>;
  /**
   * True when the backend reports this entity as builder-supported.
   * Unsupported entities are still round-trippable but render
   * read-only / raw-JSON in the UI.
   */
  supported: boolean;
  schema?: JsonSchema;
  uiSchema?: EntityUiSchema;
}

export interface SimulationDraft {
  id?: string;
  name: string;
  /**
   * The original spec captured at load time. Never mutated. The
   * serializer deep-clones it as the starting point for the
   * output spec so unknown fields survive.
   */
  rawSpec: Record<string, unknown>;
  entities: DraftEntity[];
  /**
   * Top-level keys from the original spec that no entity claims.
   * These are copied verbatim into the output spec.
   */
  unknownBlocks: Record<string, unknown>;
}
