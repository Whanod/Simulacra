"use client";

/**
 * Minimal raw-JSON fallback editor (US-008).
 *
 * Used when:
 *   - an entity has no schema at all, or
 *   - a specific field uses a structure the generic renderer does not
 *     yet handle (nested object, array of objects, union type, etc.).
 *
 * US-014 upgrades this into the full raw-spec editor. For now it is a
 * styled textarea with JSON.parse on blur — invalid input is held
 * locally until the user fixes it, so a typo never silently discards
 * the surrounding draft.
 */

import { useEffect, useState } from "react";

interface RawJsonEditorProps {
  label?: string;
  value: unknown;
  onChange: (next: unknown) => void;
  compact?: boolean;
}

function stringify(value: unknown): string {
  if (value === undefined) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function RawJsonEditor({
  label,
  value,
  onChange,
  compact = false,
}: RawJsonEditorProps) {
  const [text, setText] = useState(() => stringify(value));
  const [error, setError] = useState<string | null>(null);

  // Re-sync when the external value changes (e.g. a new entity is
  // loaded) and we are not currently holding an invalid draft.
  useEffect(() => {
    const serialized = stringify(value);
    if (!error) setText(serialized);
  }, [value, error]);

  const commit = (next: string) => {
    if (next.trim() === "") {
      setError(null);
      onChange(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(next);
      setError(null);
      onChange(parsed);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="form-group" data-raw-json>
      {label ? <label>{label} (raw JSON)</label> : null}
      <textarea
        value={text}
        rows={compact ? 3 : 8}
        spellCheck={false}
        style={{ fontFamily: "var(--font-mono)", fontSize: ".8rem" }}
        onChange={(e) => setText(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
      />
      {error ? (
        <p className="hint" style={{ color: "var(--red)" }} role="alert">
          Invalid JSON: {error}
        </p>
      ) : (
        <p className="hint">
          Unsupported field structure — edit as raw JSON. Changes commit on
          blur.
        </p>
      )}
    </div>
  );
}
