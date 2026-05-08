import type { RunSpec } from "@/lib/types/simulations";
import type { SweepConfig, SweepResult, SweepRun } from "@/lib/types/sweeps";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { specToApi } from "@/lib/api/adapters/runs";
import {
  configFromSpec,
  fromApiSweep,
  fromApiSweeps,
  recommendationsToResults,
  sweepConfigToApi,
  type ApiSweep,
  type ApiSweepRecommendations,
  type ApiSweepRowsResponse,
  type ApiSweepsListResponse,
} from "@/lib/api/adapters/sweeps";

interface ApiSweepRunResponse {
  sweep_id: string;
  data: Record<string, unknown>[];
  summary?: Record<string, unknown>;
}

interface ApiSensitivityResponse {
  data: Record<string, unknown>[];
}

export interface SensitivityRow {
  value: number;
  mean: number;
  std: number;
  min: number;
  max: number;
}

async function fetchSweepBundle(id: string): Promise<SweepRun | undefined> {
  try {
    const [sweep, rowsResp] = await Promise.all([
      apiFetch<ApiSweep>(`/sweeps/${id}`),
      apiFetch<ApiSweepRowsResponse>(`/sweeps/${id}/rows`),
    ]);
    return fromApiSweep(sweep, rowsResp.data || []);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return undefined;
    throw err;
  }
}

export const sweepService = {
  async listSweeps(): Promise<SweepRun[]> {
    const resp = await apiFetch<ApiSweepsListResponse>("/sweeps", {
      query: { limit: 100 },
    });
    return fromApiSweeps(resp.sweeps || []);
  },

  async getSweep(id: string): Promise<SweepRun | undefined> {
    return fetchSweepBundle(id);
  },

  async createSweep(
    spec: RunSpec,
    config: SweepConfig,
  ): Promise<{ sweepId: string }> {
    const body = sweepConfigToApi(specToApi(spec), config);
    const resp = await apiFetch<ApiSweepRunResponse>("/sweeps/run", {
      method: "POST",
      body,
    });
    return { sweepId: resp.sweep_id };
  },

  async getRecommendations(
    id: string,
    options: {
      objectiveMetrics: string[];
      weights?: Record<string, number>;
      lowerIsBetter?: Record<string, boolean>;
      topK?: number;
    },
  ): Promise<SweepResult[]> {
    const sweep = await apiFetch<ApiSweep>(`/sweeps/${id}`);
    const paramNames = sweep.summary?.param_names ?? [];
    const metricNames = sweep.summary?.metric_names ?? options.objectiveMetrics;

    const body = {
      objective_metrics: options.objectiveMetrics,
      weights: options.weights ?? {},
      lower_is_better: options.lowerIsBetter ?? {},
      top_k: options.topK ?? 5,
    };
    const resp = await apiFetch<ApiSweepRecommendations>(
      `/sweeps/${id}/recommendations`,
      { method: "POST", body },
    );
    return recommendationsToResults(resp, paramNames, metricNames);
  },

  async getSensitivity(
    id: string,
    param: string,
    metric: string,
  ): Promise<SensitivityRow[]> {
    const rowsResp = await apiFetch<ApiSweepRowsResponse>(`/sweeps/${id}/rows`);
    const resp = await apiFetch<ApiSensitivityResponse>("/sweeps/sensitivity", {
      method: "POST",
      body: { data: rowsResp.data, param, metric },
    });
    return (resp.data || []).map((row) => ({
      value: Number(row["value"] ?? 0),
      mean: Number(row["mean"] ?? 0),
      std: Number(row["std"] ?? 0),
      min: Number(row["min"] ?? 0),
      max: Number(row["max"] ?? 0),
    }));
  },

  configFromSpec,
};
