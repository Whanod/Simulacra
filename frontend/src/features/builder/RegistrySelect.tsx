"use client";

/**
 * Registry-driven select (US-013).
 *
 * Replaces the builder's hand-rolled `<option>` lists with a select
 * whose options come from the backend registry contract. Each option
 * corresponds to one `RegistryEntityDefinition` in the given category,
 * and unsupported entities are rendered disabled with a clear badge so
 * the user cannot silently pick a broken flow.
 *
 * Alias support: a few builder fields still hold UI-side alias keys
 * (for example `bExec = "solana"` while the backend type is
 * `solana_like`). `aliasFromBackend` / `aliasToBackend` map between
 * the two spaces without rewriting the adapters.
 */

import { useMemo } from "react";
import type {
  RegistryCategoryDefinition,
  RegistryContractResponse,
  RegistryEntityDefinition,
} from "@/lib/types/contract";
import { useRegistryContract } from "@/lib/hooks/useRegistryContract";
import { frontendKeyForBackendCategory } from "@/lib/api/adapters/registry";

export interface RegistrySelectProps {
  /** Backend category key (e.g. `"markets"`, `"execution_models"`). */
  category: string;
  /** Current UI-side value. */
  value: string;
  /** Called with the new UI-side value. */
  onChange: (next: string) => void;
  id?: string;
  "aria-label"?: string;
  className?: string;
  disabled?: boolean;
  /**
   * Optional: include only entity types that pass the predicate.
   * Use sparingly — the point of registry-driven selects is to stop
   * hiding entities. The builder uses this for a handful of
   * round-trip-blocked types only.
   */
  filter?: (entity: RegistryEntityDefinition) => boolean;
  /**
   * Maps a backend entity type to the UI-side alias used by builder
   * state. When absent the backend key is used verbatim.
   */
  aliasFromBackend?: Record<string, string>;
  /**
   * Reverse map used for the initial value lookup. Not strictly
   * needed if `value` already matches `aliasFromBackend` output, but
   * handy when callers want to coerce stale values.
   */
  aliasToBackend?: Record<string, string>;
}

function findCategory(
  contract: RegistryContractResponse | null,
  key: string,
): RegistryCategoryDefinition | undefined {
  if (!contract?.categories) return undefined;
  const frontendKey = frontendKeyForBackendCategory(key);
  return contract.categories.find(
    (c) => c.key === key || c.key === frontendKey,
  );
}

export function RegistrySelect({
  category,
  value,
  onChange,
  id,
  "aria-label": ariaLabel,
  className,
  disabled,
  filter,
  aliasFromBackend,
  aliasToBackend,
}: RegistrySelectProps) {
  const { contract, error } = useRegistryContract();
  const categoryDef = findCategory(contract, category);

  const options = useMemo(() => {
    const entities = categoryDef?.entities ?? [];
    const filtered = filter ? entities.filter(filter) : entities;
    return filtered
      .map((entity) => {
        const optionValue = aliasFromBackend?.[entity.type] ?? entity.type;
        return {
          value: optionValue,
          label: entity.label,
          supported: entity.builderSupported,
          description: entity.description,
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [categoryDef, filter, aliasFromBackend]);

  // If the caller handed us a value that still lives in UI-alias
  // space, make sure it's present in the option list. This keeps the
  // field from silently resetting when an older spec loads.
  const expandedOptions = useMemo(() => {
    if (!value) return options;
    if (options.some((o) => o.value === value)) return options;
    const backendKey = aliasToBackend?.[value] ?? value;
    return [
      ...options,
      {
        value,
        label: `${backendKey} (unknown)`,
        supported: false,
        description: undefined as string | undefined,
      },
    ];
  }, [options, value, aliasToBackend]);

  const loading = contract === null && error === null;

  const current = expandedOptions.find((o) => o.value === value);
  const currentIsUnsupported = !loading && current ? !current.supported : false;

  return (
    <div className={className}>
      <select
        id={id}
        aria-label={ariaLabel}
        value={value}
        disabled={disabled || loading}
        onChange={(e) => onChange(e.target.value)}
      >
        {loading ? (
          <option value={value}>Loading…</option>
        ) : (
          expandedOptions.map((opt) => (
            <option
              key={opt.value}
              value={opt.value}
              disabled={!opt.supported}
              title={opt.description}
            >
              {opt.label}
              {opt.supported ? "" : " (unsupported)"}
            </option>
          ))
        )}
      </select>
      {error ? (
        <p className="hint" style={{ color: "var(--red)", marginTop: 4 }}>
          Failed to load registry: {error}
        </p>
      ) : null}
      {currentIsUnsupported ? (
        <p
          className="hint"
          style={{ color: "var(--yellow)", marginTop: 4 }}
          role="alert"
        >
          This entity is marked unsupported by the backend — builds
          using it will likely fail. Pick another option.
        </p>
      ) : null}
    </div>
  );
}
