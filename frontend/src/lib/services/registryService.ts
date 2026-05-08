import type { RegistryCategory, RegTab } from "@/lib/types/registry";
import type { RegistryContractResponse } from "@/lib/types/contract";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import {
  backendCategoryForKey,
  fromApiRegistry,
  fromApiRegistryCategory,
  type ApiRegistryCategoryResponse,
  type ApiRegistryListResponse,
} from "@/lib/api/adapters/registry";

export const registryService = {
  async getCategories(): Promise<RegistryCategory[]> {
    const raw = await apiFetch<ApiRegistryListResponse>("/registry");
    return fromApiRegistry(raw);
  },

  async getCategory(key: RegTab): Promise<RegistryCategory | undefined> {
    const backendKey = backendCategoryForKey(key);
    try {
      const raw = await apiFetch<ApiRegistryCategoryResponse>(
        `/registry/${backendKey}`,
      );
      return fromApiRegistryCategory(raw, key);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return undefined;
      throw err;
    }
  },

  /**
   * Returns the enriched schema-driven contract emitted by the
   * backend `/registry` endpoint (BE-002 / BE-006).
   */
  async getContract(): Promise<RegistryContractResponse> {
    return apiFetch<RegistryContractResponse>("/registry");
  },
};
