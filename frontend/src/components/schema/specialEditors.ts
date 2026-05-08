/**
 * Special editor plugin registry (US-010).
 *
 * Some backend entities are not ergonomic to edit as a flat field
 * list — e.g. `world.markets` needs a graph editor, composite feeds
 * need a code-editor pane. The backend signals this with
 * `uiSchema.specialEditor: "<key>"`. This module is the single
 * source of truth for mapping those keys to React components.
 *
 * Fallback chain (consumed by `SchemaForm`):
 *   1. `uiSchema.specialEditor` resolves to a registered plugin →
 *      render the plugin
 *   2. Entity has a `schema` → render the generic `SchemaForm`
 *   3. Otherwise → render the `RawJsonEditor` fallback
 *
 * Plugins never reach into global state or adapters directly; they
 * receive a `DraftEntity` plus a change callback and nothing else.
 * That keeps them swappable and gives US-012..US-015 a single
 * migration target if the draft model shape moves.
 *
 * US-011 will register the real `world-markets-graph` plugin;
 * US-010 ships a no-op fixture plugin so the registration path is
 * exercised end-to-end from the moment this module lands.
 */

import type { ComponentType } from "react";
import type { DraftEntity } from "@/lib/types/drafts";

export interface SpecialEditorProps {
  entity: DraftEntity;
  onChange: (params: Record<string, unknown>) => void;
}

export type SpecialEditorComponent = ComponentType<SpecialEditorProps>;

/**
 * Internal registry. Kept as a plain object so module-load order is
 * deterministic and the registry can be iterated in tests without
 * depending on Map insertion semantics.
 */
const registry: Record<string, SpecialEditorComponent> = {};

/**
 * Registers (or overwrites) a plugin for a `specialEditor` key.
 * Overwriting is intentional — the only caller is module-level
 * registration, and silent failure on a duplicate would hide real
 * bugs. Tests use this to swap plugins in/out.
 */
export function registerSpecialEditor(
  key: string,
  component: SpecialEditorComponent,
): void {
  registry[key] = component;
}

/**
 * Returns the plugin registered for `key`, or `undefined` if none.
 * Missing plugins are a normal case: the caller's fallback chain
 * takes over (generic schema renderer, then raw JSON).
 */
export function getSpecialEditor(
  key: string | undefined,
): SpecialEditorComponent | undefined {
  if (!key) return undefined;
  return registry[key];
}

/**
 * Test helper: returns the full list of registered plugin keys.
 * Not part of the plugin contract — intended for assertions only.
 */
export function listRegisteredSpecialEditors(): string[] {
  return Object.keys(registry).sort();
}

/**
 * Test helper: removes a plugin so tests can reset state between
 * runs. Not intended for production code paths.
 */
export function unregisterSpecialEditor(key: string): void {
  delete registry[key];
}
