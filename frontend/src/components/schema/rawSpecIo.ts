/**
 * Pure helpers for the raw-spec fallback editor (US-014).
 *
 * The UI layer (RawSpecEditor.tsx) is a thin React wrapper that
 * delegates parsing, serialization, and error classification to the
 * functions in this module. Keeping the logic here makes it testable
 * without a React transform.
 *
 * The editor is backed by the generic draft model: round-tripping a
 * parsed spec through `draftFromApiSpec` + `draftToApiSpec` guarantees
 * that unknown backend categories and fields survive raw editing, so
 * switching between structured and raw mode is no-loss for anything
 * the draft model can preserve.
 */

import { draftFromApiSpec, draftToApiSpec } from "@/lib/api/adapters/drafts";
import type { SimulationDraft } from "@/lib/types/drafts";
import type { RegistryContractResponse } from "@/lib/types/contract";

export type RawSpecParseResult =
  | { ok: true; draft: SimulationDraft }
  | { ok: false; error: string };

export interface ParseRawSpecOptions {
  /** Optional registry contract used to enrich draft entities. */
  contract?: RegistryContractResponse;
  /** Optional draft metadata carried across the parse. */
  name?: string;
  id?: string;
}

export function serializeDraftToText(draft: SimulationDraft): string {
  try {
    return JSON.stringify(draftToApiSpec(draft), null, 2);
  } catch {
    return "";
  }
}

export function parseRawSpecText(
  text: string,
  options: ParseRawSpecOptions = {},
): RawSpecParseResult {
  const trimmed = text.trim();
  if (trimmed === "") {
    return { ok: false, error: "Spec cannot be empty" };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed)
  ) {
    return { ok: false, error: "Spec must be a JSON object" };
  }
  const draft = draftFromApiSpec(parsed as Record<string, unknown>, {
    contract: options.contract,
    name: options.name,
    id: options.id,
  });
  return { ok: true, draft };
}
