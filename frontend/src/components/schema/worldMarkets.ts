/**
 * Pure helpers for the `world-markets-graph` plugin (US-011).
 *
 * Lives in its own .ts module so the unit tests can exercise the
 * read / sanitize / write logic without dragging the `.tsx` file
 * through vitest's JSX-less transform path.
 */

export interface WorldMarketBlock {
  id: string;
  type: string;
  label: string;
  tokens: string[];
}

export interface WorldMarketLink {
  from: string;
  to: string;
  token: string;
}

export function isPlainObject(
  value: unknown,
): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Coerces an arbitrary value into a `WorldMarketBlock` by filling in
 * sensible defaults for any missing field. Never throws — a totally
 * bogus input (null, undefined, number) still yields a valid block
 * with the provided fallback id so the caller gets a renderable
 * item instead of a stack trace.
 */
export function sanitizeBlock(
  value: unknown,
  fallbackId: string,
): WorldMarketBlock {
  const raw = isPlainObject(value) ? value : {};
  const id =
    typeof raw.id === "string" && raw.id.length > 0 ? raw.id : fallbackId;
  const type = typeof raw.type === "string" ? raw.type : "cfamm";
  const label =
    typeof raw.label === "string" && raw.label.length > 0
      ? raw.label
      : `${type.toUpperCase()}-${id.slice(-3)}`;
  const tokens = Array.isArray(raw.tokens)
    ? raw.tokens.filter((t): t is string => typeof t === "string")
    : ["TKN-A", "TKN-B"];
  return { id, type, label, tokens };
}

/**
 * Returns a valid link or `null` when required fields are missing.
 * Links without a `from` or `to` cannot be rendered and should be
 * dropped on read — preserving them would give the user a phantom
 * edge they cannot see or clean up.
 */
export function sanitizeLink(value: unknown): WorldMarketLink | null {
  if (!isPlainObject(value)) return null;
  const from = typeof value.from === "string" ? value.from : undefined;
  const to = typeof value.to === "string" ? value.to : undefined;
  const token = typeof value.token === "string" ? value.token : "";
  if (!from || !to) return null;
  return { from, to, token };
}

export function readBlocks(
  params: Record<string, unknown>,
): WorldMarketBlock[] {
  const raw = params.markets;
  if (!Array.isArray(raw)) return [];
  return raw.map((v, i) => sanitizeBlock(v, `m${i + 1}`));
}

export function readLinks(
  params: Record<string, unknown>,
): WorldMarketLink[] {
  const raw = params.links;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((v) => sanitizeLink(v))
    .filter((l): l is WorldMarketLink => l !== null);
}

/**
 * Mints a fresh block id that does not collide with any id in
 * `existing`. The plugin prefers `m1`, `m2`, … to match the
 * existing convention so diffs stay readable.
 */
export function makeBlockId(existing: WorldMarketBlock[]): string {
  const taken = new Set(existing.map((b) => b.id));
  let i = existing.length + 1;
  while (taken.has(`m${i}`)) i += 1;
  return `m${i}`;
}

/**
 * Deterministic view-only layout for a list of blocks. The graph
 * editor overrides positions on drag but seeds from this function
 * so the canvas looks the same every time the entity is opened.
 */
export function seedLayout(
  blocks: WorldMarketBlock[],
  columnGap = 180,
  rowGap = 100,
): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {};
  blocks.forEach((block, i) => {
    const col = i % 3;
    const row = Math.floor(i / 3);
    out[block.id] = {
      x: 40 + col * columnGap,
      y: 40 + row * rowGap,
    };
  });
  return out;
}
