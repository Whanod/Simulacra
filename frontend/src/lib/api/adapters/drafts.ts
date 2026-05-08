/**
 * Draft adapter (US-005 / US-006).
 *
 * Converts a backend simulation spec to a `SimulationDraft` and back.
 * The adapter is intentionally tolerant: unknown categories, unknown
 * entity types, and unknown per-block fields all survive the round
 * trip.
 *
 * Merge rule:
 *   draftToApiSpec starts with a deep clone of `draft.rawSpec` and,
 *   for each entity, walks `entity.configPath` to the target block
 *   and key-by-key overlays `entity.params` on top of the cloned
 *   block. Keys present in `entity.raw` but absent from `params` are
 *   preserved as-is (they come through via the clone).
 */

import type {
  RegistryContractResponse,
  RegistryEntityDefinition,
} from "@/lib/types/contract";
import type { DraftEntity, SimulationDraft } from "@/lib/types/drafts";

type Json = Record<string, unknown>;

function isPlainObject(value: unknown): value is Json {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function deepClone<T>(value: T): T {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    return value.map((v) => deepClone(v)) as unknown as T;
  }
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    out[k] = deepClone(v);
  }
  return out as unknown as T;
}

function splitPath(path: string): string[] {
  return path.split(".").filter((seg) => seg.length > 0);
}

function getAtPath(root: unknown, path: string): unknown {
  const segments = splitPath(path);
  let cursor: unknown = root;
  for (const seg of segments) {
    if (cursor === undefined || cursor === null) return undefined;
    if (Array.isArray(cursor)) {
      const idx = Number.parseInt(seg, 10);
      if (Number.isNaN(idx)) return undefined;
      cursor = cursor[idx];
      continue;
    }
    if (isPlainObject(cursor)) {
      cursor = (cursor as Json)[seg];
      continue;
    }
    return undefined;
  }
  return cursor;
}

/**
 * Sets a value at `path` inside `root`, creating intermediate
 * containers as needed. When a segment is numeric and its parent is
 * an array, the array index is used; otherwise plain-object keys are
 * used. Returns the (possibly newly created) leaf container or
 * undefined if a segment traverses through a non-container value.
 */
function setAtPath(root: Json, path: string, value: unknown): void {
  const segments = splitPath(path);
  if (segments.length === 0) return;
  let cursor: Json | unknown[] = root;
  for (let i = 0; i < segments.length - 1; i++) {
    const seg = segments[i];
    const next = segments[i + 1];
    const wantArray = /^\d+$/.test(next);
    if (Array.isArray(cursor)) {
      const idx = Number.parseInt(seg, 10);
      const child: unknown = cursor[idx];
      if (!isPlainObject(child) && !Array.isArray(child)) {
        const created: Json | unknown[] = wantArray ? [] : {};
        cursor[idx] = created;
        cursor = created;
      } else {
        cursor = child as Json | unknown[];
      }
    } else {
      const child: unknown = (cursor as Json)[seg];
      if (!isPlainObject(child) && !Array.isArray(child)) {
        const created: Json | unknown[] = wantArray ? [] : {};
        (cursor as Json)[seg] = created;
        cursor = created;
      } else {
        cursor = child as Json | unknown[];
      }
    }
  }
  const leaf = segments[segments.length - 1];
  if (Array.isArray(cursor)) {
    const idx = Number.parseInt(leaf, 10);
    if (!Number.isNaN(idx)) cursor[idx] = value;
  } else {
    (cursor as Json)[leaf] = value;
  }
}

/**
 * Overlays `params` on top of `target` in place, key-by-key. Keys
 * in `target` that are absent from `params` are preserved. This is
 * the core of the US-005 merge rule.
 */
function overlayParams(target: Json, params: Json): void {
  for (const [key, value] of Object.entries(params)) {
    target[key] = value;
  }
}

interface EntityDescriptor {
  category: string;
  type: string;
  configPath: string;
}

function typeFromBlock(
  block: Json | undefined,
  fallback: string,
  typeKey = "type",
): string {
  if (block && typeof block[typeKey] === "string") {
    return block[typeKey] as string;
  }
  return fallback;
}

/**
 * Walks a backend spec and yields the known entity descriptors.
 * Unknown top-level keys that this function does not claim survive
 * via the draft's `unknownBlocks` / `rawSpec` preservation path.
 */
function collectDescriptors(spec: Json): EntityDescriptor[] {
  const descriptors: EntityDescriptor[] = [];

  const market = spec.market;
  if (isPlainObject(market)) {
    descriptors.push({
      category: "markets",
      type: typeFromBlock(market, "unknown"),
      configPath: "market",
    });
  }

  const clock = spec.clock;
  if (isPlainObject(clock)) {
    descriptors.push({
      category: "clocks",
      type: typeFromBlock(clock, "unknown"),
      configPath: "clock",
    });
  }

  const execution = spec.execution;
  if (isPlainObject(execution)) {
    descriptors.push({
      category: "execution_models",
      type: typeFromBlock(execution, "unknown", "model"),
      configPath: "execution",
    });
    if (typeof execution.ordering === "string") {
      descriptors.push({
        category: "orderings",
        type: execution.ordering,
        configPath: "execution.ordering",
      });
    }
    if (typeof execution.cost_model === "string") {
      descriptors.push({
        category: "gas_models",
        type: execution.cost_model,
        configPath: "execution.cost_model",
      });
    }
  }

  const fee = spec.fee_model;
  if (isPlainObject(fee)) {
    descriptors.push({
      category: "fee_models",
      type: typeFromBlock(fee, "unknown"),
      configPath: "fee_model",
    });
  }

  const feeds = spec.feeds;
  if (Array.isArray(feeds)) {
    feeds.forEach((feed, idx) => {
      if (isPlainObject(feed)) {
        descriptors.push({
          category: "feeds",
          type: typeFromBlock(feed, "unknown"),
          configPath: `feeds.${idx}`,
        });
      }
    });
  }

  const agents = spec.agents;
  if (isPlainObject(agents)) {
    const roleParams = agents.role_params;
    if (isPlainObject(roleParams)) {
      for (const roleKey of Object.keys(roleParams)) {
        descriptors.push({
          category: "agents",
          type: roleKey,
          configPath: `agents.role_params.${roleKey}`,
        });
      }
    }
  }

  const config = spec.config;
  if (isPlainObject(config)) {
    if (typeof config.information_filter === "string") {
      descriptors.push({
        category: "information_filters",
        type: config.information_filter,
        configPath: "config.information_filter",
      });
    }
  }

  return descriptors;
}

/**
 * Top-level spec keys that `collectDescriptors` claims. Anything
 * not in this set is copied verbatim into `unknownBlocks` so it
 * survives the round trip.
 */
const CLAIMED_TOP_LEVEL_KEYS = new Set([
  "market",
  "world",
  "clock",
  "execution",
  "fee_model",
  "feeds",
  "agents",
  "config",
]);

function findContractEntity(
  contract: RegistryContractResponse | undefined,
  category: string,
  type: string,
): RegistryEntityDefinition | undefined {
  if (!contract) return undefined;
  for (const cat of contract.categories) {
    for (const entity of cat.entities) {
      if (entity.category === category && entity.type === type) return entity;
    }
  }
  return undefined;
}

function extractRawBlock(spec: Json, configPath: string): Json {
  const value = getAtPath(spec, configPath);
  if (isPlainObject(value)) return deepClone(value);
  // Scalar target (e.g. execution.ordering = "fifo") — wrap in a
  // synthetic object so the draft has a uniform shape. Serialization
  // unwraps scalar-valued single-key objects back to scalars.
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return { __value: value };
  }
  return {};
}

function extractParamsFromRaw(raw: Json): Json {
  if ("__value" in raw && Object.keys(raw).length === 1) {
    return { __value: raw.__value };
  }
  const out: Json = {};
  for (const [k, v] of Object.entries(raw)) {
    if (k === "type" || k === "model") continue;
    out[k] = deepClone(v);
  }
  return out;
}

export interface DraftFromSpecOptions {
  contract?: RegistryContractResponse;
  name?: string;
  id?: string;
}

export function draftFromApiSpec(
  spec: Json,
  options: DraftFromSpecOptions = {},
): SimulationDraft {
  const rawSpec = deepClone(spec);
  const descriptors = collectDescriptors(rawSpec);

  const entities: DraftEntity[] = descriptors.map((d) => {
    const raw = extractRawBlock(rawSpec, d.configPath);
    const params = extractParamsFromRaw(raw);
    const contractEntity = findContractEntity(
      options.contract,
      d.category,
      d.type,
    );
    return {
      category: d.category,
      type: d.type,
      label: contractEntity?.label ?? d.type,
      configPath: d.configPath,
      params,
      raw,
      supported: contractEntity?.builderSupported ?? true,
      schema: contractEntity?.schema,
      uiSchema: contractEntity?.uiSchema,
    };
  });

  const unknownBlocks: Json = {};
  for (const [key, value] of Object.entries(rawSpec)) {
    if (!CLAIMED_TOP_LEVEL_KEYS.has(key)) {
      unknownBlocks[key] = deepClone(value);
    }
  }

  return {
    id: options.id,
    name: options.name ?? "Untitled Simulation",
    rawSpec,
    entities,
    unknownBlocks,
  };
}

/**
 * Serializes a draft back to an API spec. Implements the US-005
 * merge rule: clone `rawSpec`, then for each entity overlay its
 * `params` on the target block at `configPath`, key-by-key.
 *
 * Scalar-valued blocks (e.g. `execution.ordering = "fifo"`) are
 * represented in the draft as `{ __value: ... }` wrappers; this
 * function unwraps them on serialization.
 */
export function draftToApiSpec(draft: SimulationDraft): Json {
  const out: Json = deepClone(draft.rawSpec);

  for (const entity of draft.entities) {
    const existing = getAtPath(out, entity.configPath);

    // Scalar wrapper — unwrap and write the scalar directly.
    if (
      isPlainObject(entity.raw) &&
      Object.keys(entity.raw).length === 1 &&
      "__value" in entity.raw
    ) {
      const scalar =
        entity.params.__value !== undefined
          ? entity.params.__value
          : entity.raw.__value;
      setAtPath(out, entity.configPath, scalar);
      continue;
    }

    if (!isPlainObject(existing)) {
      // The original value at this path was not an object; write
      // params as a fresh block rather than drop it.
      setAtPath(out, entity.configPath, deepClone(entity.params));
      continue;
    }
    overlayParams(existing, entity.params);
  }

  // Re-attach unknown top-level blocks that were captured at load
  // time but may have been removed from rawSpec by an upstream caller.
  for (const [key, value] of Object.entries(draft.unknownBlocks)) {
    if (!(key in out)) out[key] = deepClone(value);
  }

  return out;
}
