"use client";

/**
 * No-op fixture plugin (US-010).
 *
 * This component exists for one reason: to prove the special-editor
 * registration path end-to-end before US-011 ports the real
 * `world-markets-graph` editor. It renders a read-only summary of
 * the entity's params and exposes an "Edit raw" toggle that hands
 * off to the existing `RawJsonEditor` so plugins that do not yet
 * have a custom UI still let users make changes.
 *
 * It is registered under the key `noop-preview` by
 * `src/components/schema/registerSpecialEditors.ts`. The key is
 * intentionally distinct from any real `specialEditor` key the
 * backend emits (`world-markets-graph`, `code-editor`) so the
 * fixture never accidentally shadows a real editor.
 */

import { useState } from "react";
import type { SpecialEditorProps } from "./specialEditors";
import { RawJsonEditor } from "./RawJsonEditor";

export function NoopSpecialEditor({ entity, onChange }: SpecialEditorProps) {
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div
      className="special-editor noop-special-editor"
      data-special-editor="noop-preview"
    >
      <div
        className="hint"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <span>
          {"Plugin: "}
          <strong>noop-preview</strong>
          {" - fixture editor for "}
          <code>{`${entity.category}/${entity.type}`}</code>
        </span>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => setShowRaw((v) => !v)}
        >
          {showRaw ? "Close raw" : "Edit raw"}
        </button>
      </div>
      {showRaw ? (
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
      ) : (
        <pre
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: ".78rem",
            background: "var(--bg-2)",
            padding: 8,
            borderRadius: "var(--radius)",
            overflow: "auto",
            margin: 0,
          }}
        >
          {JSON.stringify(entity.params, null, 2)}
        </pre>
      )}
    </div>
  );
}
