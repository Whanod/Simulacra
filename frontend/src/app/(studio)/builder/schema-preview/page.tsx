"use client";

/**
 * Internal preview route for the generic schema renderer (US-008).
 *
 * Fetches the live registry contract and renders a `SchemaForm` for
 * one representative entity per category so the renderer can be
 * exercised in a browser without wiring it into the main builder
 * flow (US-012..US-014). This route is a dev tool, not a shipped
 * user surface — later stories replace it with the real builder
 * integration.
 */

import { useEffect, useMemo, useState } from "react";
import Topbar from "@/components/shell/Topbar";
import Card from "@/components/ui/Card";
import { SchemaForm } from "@/components/schema/SchemaForm";
// Side-effect import: wires built-in plugins (noop-preview fixture,
// and eventually world-markets-graph) into the static registry.
import "@/components/schema/registerSpecialEditors";
import { registryService } from "@/lib/services/registryService";
import type {
  RegistryContractResponse,
  RegistryEntityDefinition,
} from "@/lib/types/contract";
import type { DraftEntity } from "@/lib/types/drafts";

function entityToDraft(entity: RegistryEntityDefinition): DraftEntity {
  const defaults = entity.defaults ?? {};
  return {
    category: entity.category,
    type: entity.type,
    label: entity.label,
    configPath: `${entity.category}.${entity.type}`,
    params: { ...defaults },
    raw: { ...defaults },
    supported: entity.builderSupported,
    schema: entity.schema,
    uiSchema: entity.uiSchema,
  };
}

/**
 * Synthetic draft whose `uiSchema.specialEditor` points at the
 * US-010 `noop-preview` fixture. It exists purely to exercise the
 * plugin registration path end-to-end in the browser — every other
 * preview card comes from the real backend contract.
 */
const NOOP_PLUGIN_DRAFT: DraftEntity = {
  category: "fixture",
  type: "noop-plugin",
  label: "Fixture: Noop Plugin",
  configPath: "fixture.noop",
  params: { sample: "value", nested: { a: 1, b: 2 } },
  raw: {},
  supported: true,
  schema: undefined,
  uiSchema: { specialEditor: "noop-preview" },
};

/**
 * Seed markets + links for the `world-markets-graph` preview card
 * (US-011). The real backend `markets:world` entity ships empty
 * defaults because its params are owned entirely by the plugin; we
 * pre-populate two markets and one link here so the graph has
 * something visible on first mount.
 */
const WORLD_SEED_PARAMS = {
  markets: [
    { id: "m1", type: "cfamm", label: "CFAMM-m1", tokens: ["ETH", "USDC"] },
    { id: "m2", type: "clob", label: "CLOB-m2", tokens: ["BTC", "USDC"] },
  ],
  links: [{ from: "m1", to: "m2", token: "USDC" }],
};

export default function SchemaPreviewPage() {
  const [contract, setContract] = useState<RegistryContractResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draftsByKey, setDraftsByKey] = useState<Record<string, DraftEntity>>({
    "fixture:noop-plugin": NOOP_PLUGIN_DRAFT,
  });

  useEffect(() => {
    let cancelled = false;
    registryService
      .getContract()
      .then((resp) => {
        if (cancelled) return;
        setContract(resp);
        const seeded: Record<string, DraftEntity> = {
          "fixture:noop-plugin": NOOP_PLUGIN_DRAFT,
        };
        // Seed one representative entity per category so every
        // category shows up, plus any entity that declares sections
        // so the basic/advanced toggle has visible content. We key
        // by "category:type" which also sorts the previews
        // predictably for e2e assertions.
        for (const cat of resp.categories) {
          const firstSupported = cat.entities.find((e) => e.builderSupported);
          if (firstSupported) {
            const key = `${firstSupported.category}:${firstSupported.type}`;
            seeded[key] = entityToDraft(firstSupported);
          }
          for (const entity of cat.entities) {
            if (!entity.builderSupported) continue;
            const sections = entity.uiSchema?.sections;
            const special = entity.uiSchema?.specialEditor;
            if ((sections && sections.length > 0) || special) {
              const key = `${entity.category}:${entity.type}`;
              const draft = entityToDraft(entity);
              if (special === "world-markets-graph") {
                draft.params = { ...WORLD_SEED_PARAMS };
              }
              seeded[key] = draft;
            }
          }
        }
        setDraftsByKey(seeded);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const updateDraft = (key: string, params: Record<string, unknown>) => {
    setDraftsByKey((prev) => {
      const current = prev[key];
      if (!current) return prev;
      return { ...prev, [key]: { ...current, params } };
    });
  };

  const drafts = useMemo(
    () =>
      Object.entries(draftsByKey)
        .map(([key, entity]) => ({ key, entity }))
        .sort((a, b) => a.key.localeCompare(b.key)),
    [draftsByKey],
  );

  return (
    <>
      <Topbar title="Schema Renderer Preview" />
      <div id="content">
        {error ? (
          <Card title="Error">
            <p className="hint" style={{ color: "var(--red)" }}>
              Failed to load registry contract: {error}
            </p>
          </Card>
        ) : !contract ? (
          <Card title="Loading">
            <p className="hint">Fetching registry contract…</p>
          </Card>
        ) : (
          <div className="grid-2">
            {drafts.map(({ key, entity }) => (
              <Card
                key={key}
                title={`${entity.category} / ${entity.label}`}
              >
                <SchemaForm
                  entity={entity}
                  onChange={(params) => updateDraft(key, params)}
                />
                <details style={{ marginTop: 12 }}>
                  <summary className="hint">Current params</summary>
                  <pre
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: ".78rem",
                      background: "var(--bg-2)",
                      padding: 8,
                      borderRadius: "var(--radius)",
                      overflow: "auto",
                    }}
                  >
                    {JSON.stringify(entity.params, null, 2)}
                  </pre>
                </details>
              </Card>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
