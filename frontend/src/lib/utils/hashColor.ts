/**
 * Deterministic hash-based color utilities (US-015).
 *
 * The frontend no longer owns a closed mapping of agent roles, event
 * types, or other backend-defined keys to colors. Read-only views need
 * stable, legible colors for arbitrary keys without introducing a
 * coverage map that has to be edited every time the backend ships a
 * new role or event kind.
 *
 * These helpers take a string key and return a deterministic palette
 * entry. Two calls with the same key always return the same entry, so
 * charts, legends, and table rows stay visually consistent across
 * renders.
 */

/**
 * Palette CSS variables declared in `src/app/globals.css`. Ordering is
 * stable: picks from the hash index must not shift when we add colors,
 * so new entries should only be appended.
 */
const PALETTE_VARS = [
  "var(--accent)",
  "var(--green)",
  "var(--yellow)",
  "var(--purple)",
  "var(--cyan)",
  "var(--orange)",
  "var(--red)",
] as const;

/**
 * CSS classes in `.event-log .ev-type.*` — same stability rules as
 * `PALETTE_VARS`. Unknown event types hash into this set so the
 * existing event-log styling renders them without new CSS.
 */
const EVENT_CLASSES = [
  "trade",
  "lp",
  "oracle",
  "reward",
  "fail",
] as const;

export type EventClass = (typeof EVENT_CLASSES)[number];

/**
 * FNV-1a 32-bit hash. Small, fast, and dependency-free. We only need
 * stability across runs of the same build, not cryptographic quality.
 */
function fnv1a(input: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    // 32-bit FNV prime: 16777619
    hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
  }
  return hash >>> 0;
}

/**
 * Returns a palette CSS var for `key`. Stable across calls; safe for
 * use in inline styles and chart series colors.
 */
export function hashColorVar(key: string): string {
  const idx = fnv1a(key) % PALETTE_VARS.length;
  return PALETTE_VARS[idx];
}

/**
 * Returns an event-log CSS class for `type`. Stable across calls; the
 * return value is always one of `EVENT_CLASSES` so existing
 * `.event-log .ev-type.*` styles apply without new CSS.
 */
export function hashEventClass(type: string): EventClass {
  const idx = fnv1a(type) % EVENT_CLASSES.length;
  return EVENT_CLASSES[idx];
}
