/**
 * Frontend category key. Historically a closed union of known categories;
 * the schema-driven refactor (US-001) reopens it so unknown backend
 * categories survive parse. Known categories still use their curated
 * "reg-<name>" keys; unknowns get a synthesized "reg-<backend_key>".
 */
export type RegTab = string;

export interface RegistryEntry {
  /** Human-readable label from the backend contract. */
  name: string;
  /**
   * Raw backend type identifier (e.g. `"cfamm"`). US-016: kept
   * alongside `name` so registry → builder seeding uses the type the
   * builder actually recognizes, not the human label, which drifted
   * apart after BE-003 humanized labels.
   */
  type: string;
  description: string;
  params?: string;
  badges?: { label: string; variant: "green" | "blue" | "purple" | "yellow" | "red" }[];
  disabled?: boolean;
}

export interface RegistryCategory {
  key: string;
  label: string;
  description?: string;
  entries: RegistryEntry[];
}
