import type { RegistryCategory, RegistryEntry } from "@/lib/types/registry";
import type { RegistryContractResponse } from "@/lib/types/contract";
import { describeType } from "@/lib/descriptions/registry";

/**
 * Backend /registry response shape (BE-002). The legacy
 * `dict[str, list[str]]` shape was removed in BE-006.
 */
export type ApiRegistryListResponse = RegistryContractResponse;

/**
 * Backend /registry/{category} response shape. Equivalent to one
 * entry in `ApiRegistryListResponse.categories`.
 */
export type ApiRegistryCategoryResponse =
  RegistryContractResponse["categories"][number];

type ApiEntity = ApiRegistryCategoryResponse["entities"][number];

type LegacyBadge = NonNullable<RegistryEntry["badges"]>[number];
type LegacyBadgeVariant = LegacyBadge["variant"];

const ALLOWED_VARIANTS: ReadonlySet<LegacyBadgeVariant> = new Set([
  "green",
  "blue",
  "purple",
  "yellow",
  "red",
]);

function coerceBadges(
  raw: ApiEntity["badges"] | null | undefined,
): RegistryEntry["badges"] {
  if (!raw) return undefined;
  return raw.map((badge) => {
    const variant = ALLOWED_VARIANTS.has(badge.variant as LegacyBadgeVariant)
      ? (badge.variant as LegacyBadgeVariant)
      : "blue";
    return { label: badge.label, variant };
  });
}

function entityToLegacyEntry(entity: ApiEntity): RegistryEntry {
  const description = entity.description ?? "";
  const base: RegistryEntry = {
    name: entity.label,
    type: entity.type,
    description: description || describeType(entity.category, entity.type).description,
  };
  const badges = coerceBadges(entity.badges);
  if (badges && badges.length > 0) base.badges = badges;
  if (entity.builderSupported === false) base.disabled = true;
  return base;
}

function categoryToLegacy(
  category: ApiRegistryCategoryResponse,
): RegistryCategory {
  return {
    key: category.key,
    label: category.label,
    description: category.description || undefined,
    entries: category.entities.map(entityToLegacyEntry),
  };
}

export function fromApiRegistry(raw: ApiRegistryListResponse): RegistryCategory[] {
  return raw.categories
    .slice()
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
    .map(categoryToLegacy);
}

export function fromApiRegistryCategory(
  raw: ApiRegistryCategoryResponse,
  _key: string,
): RegistryCategory {
  return categoryToLegacy(raw);
}

/** Map a frontend "reg-*" key back to the backend category string. */
const FRONTEND_TO_BACKEND: Record<string, string> = {
  "reg-markets": "markets",
  "reg-agents": "agents",
  "reg-clocks": "clocks",
  "reg-ordering": "orderings",
  "reg-gas": "gas_models",
  "reg-fees": "fee_models",
  "reg-feeds": "feeds",
  "reg-exec": "execution_models",
  "reg-information": "information_filters",
};

export function backendCategoryForKey(key: string): string {
  if (FRONTEND_TO_BACKEND[key]) return FRONTEND_TO_BACKEND[key];
  if (key.startsWith("reg-")) return key.slice("reg-".length);
  return key;
}

const BACKEND_TO_FRONTEND: Record<string, string> = Object.fromEntries(
  Object.entries(FRONTEND_TO_BACKEND).map(([k, v]) => [v, k]),
);

export function frontendKeyForBackendCategory(category: string): string {
  return BACKEND_TO_FRONTEND[category] ?? `reg-${category}`;
}
