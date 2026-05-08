"use client";

/**
 * Raw spec fallback editor (US-014).
 *
 * Renders the full simulation spec as an editable JSON textarea. Users
 * drop into this view when the structured builder cannot represent a
 * field or category — typically partially-supported backend schemas.
 *
 * The editor is backed by the generic draft model so round-tripping
 * preserves unknown backend categories, types, and fields.
 *
 * On blur, the text is parsed via `parseRawSpecText`. Parse failures
 * hold locally (the draft is not mutated) so a typo never discards
 * surrounding data. Backend validation errors (`validationErrors` prop)
 * are rendered alongside parse errors so the user sees everything
 * relevant against the current draft in one place.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { SimulationDraft } from "@/lib/types/drafts";
import type { RegistryContractResponse } from "@/lib/types/contract";
import {
  parseRawSpecText,
  serializeDraftToText,
} from "./rawSpecIo";

interface RawSpecEditorProps {
  draft: SimulationDraft;
  onChange: (draft: SimulationDraft) => void;
  validationErrors?: string[];
  contract?: RegistryContractResponse;
}

export function RawSpecEditor({
  draft,
  onChange,
  validationErrors,
  contract,
}: RawSpecEditorProps) {
  const serialized = useMemo(() => serializeDraftToText(draft), [draft]);
  const [text, setText] = useState<string>(serialized);
  const [parseError, setParseError] = useState<string | null>(null);

  // Re-sync the textarea to the external draft whenever it changes
  // (e.g. after switching back into raw mode). Do not clobber the
  // user's local draft while they're holding an invalid edit.
  useEffect(() => {
    if (!parseError) setText(serialized);
  }, [serialized, parseError]);

  const commit = useCallback(
    (nextText: string) => {
      const result = parseRawSpecText(nextText, {
        contract,
        name: draft.name,
        id: draft.id,
      });
      if (!result.ok) {
        setParseError(result.error);
        return;
      }
      setParseError(null);
      onChange(result.draft);
    },
    [contract, draft.name, draft.id, onChange],
  );

  return (
    <div className="raw-spec-editor" data-testid="raw-spec-editor">
      <p
        className="hint"
        style={{ marginTop: 0, marginBottom: 8 }}
      >
        Edit the full simulation spec as JSON. Changes commit on blur and
        are round-tripped through the draft model so unknown backend
        fields survive. Invalid JSON is held locally — your draft is not
        mutated until you fix the error.
      </p>
      <textarea
        data-testid="raw-spec-editor-textarea"
        aria-label="Raw simulation spec JSON"
        rows={28}
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        style={{
          width: "100%",
          minHeight: 420,
          fontFamily: "var(--font-mono)",
          fontSize: ".78rem",
          lineHeight: 1.5,
          padding: 12,
          background: "var(--surface-1)",
          color: "var(--text-1)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          resize: "vertical",
        }}
      />
      {parseError ? (
        <p
          className="hint"
          role="alert"
          data-testid="raw-spec-editor-parse-error"
          style={{ color: "var(--red)", marginTop: 8 }}
        >
          Invalid spec: {parseError}
        </p>
      ) : null}
      {validationErrors && validationErrors.length > 0 ? (
        <ul
          data-testid="raw-spec-editor-validation-errors"
          style={{
            color: "var(--red)",
            fontSize: ".85rem",
            paddingLeft: 18,
            margin: "12px 0 0 0",
          }}
        >
          {validationErrors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
