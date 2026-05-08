import { apiFetch } from "@/lib/api/client";
import {
  fromApiAllMarkets,
  fromApiEngineEvents,
  fromApiParameters,
  fromApiStatus,
  fromApiStep,
  fromApiViolations,
  type ApiAllMarketStates,
  type ApiEventResponse,
  type ApiMarketSnapshotRaw,
  type ApiParameterStore,
  type ApiSimulationStatus,
  type ApiStepResponse,
  type ApiViolationsResponse,
  type ParameterStoreView,
  type RoundDelta,
  type ViolationRow,
} from "@/lib/api/adapters/runner";
import type { EvEntry } from "@/lib/types";

export interface SimulationStatus {
  simulationId: string;
  runId: string | null;
  currentRound: number;
  isComplete: boolean;
  cancelled: boolean;
}

export interface ForkResult {
  simulationId: string;
  runId: string;
  currentRound: number;
  isComplete: boolean;
}

interface ApiForkResponse {
  simulation_id: string;
  run_id: string;
  current_round?: number;
  is_complete?: boolean;
}

export const runnerService = {
  async getStatus(simulationId: string): Promise<SimulationStatus> {
    const raw = await apiFetch<ApiSimulationStatus>(
      `/simulations/${simulationId}/status`,
    );
    return fromApiStatus(raw);
  },

  async step(
    simulationId: string,
    prior?: { totalCumulativeVolume: number },
    marketName?: string | null,
  ): Promise<RoundDelta> {
    const raw = await apiFetch<ApiStepResponse>(
      `/simulations/${simulationId}/step`,
      { method: "POST" },
    );
    return fromApiStep(raw, prior, marketName);
  },

  async cancel(simulationId: string): Promise<void> {
    await apiFetch(`/simulations/${simulationId}/cancel`, { method: "POST" });
  },

  async deleteEngine(simulationId: string): Promise<void> {
    await apiFetch(`/simulations/${simulationId}`, { method: "DELETE" });
  },

  async getAllMarkets(
    simulationId: string,
  ): Promise<Array<{ name: string; snapshot: ApiMarketSnapshotRaw }>> {
    const raw = await apiFetch<ApiAllMarketStates>(
      `/simulations/${simulationId}/markets`,
    );
    return fromApiAllMarkets(raw);
  },

  async getParameters(simulationId: string): Promise<ParameterStoreView> {
    const raw = await apiFetch<ApiParameterStore>(
      `/simulations/${simulationId}/parameters`,
    );
    return fromApiParameters(raw);
  },

  async setParameter(
    simulationId: string,
    key: string,
    value: unknown,
  ): Promise<void> {
    await apiFetch(`/simulations/${simulationId}/parameters`, {
      method: "PUT",
      body: { key, value },
    });
  },

  async scheduleParameter(
    simulationId: string,
    key: string,
    value: unknown,
    executeAtRound: number,
  ): Promise<void> {
    await apiFetch(`/simulations/${simulationId}/parameters/schedule`, {
      method: "POST",
      body: { key, value, execute_at_round: executeAtRound },
    });
  },

  async attachValidationHook(
    simulationId: string,
    checks: string[] = ["solvency", "reserves"],
  ): Promise<void> {
    const params = new URLSearchParams();
    for (const c of checks) params.append("checks", c);
    await apiFetch(`/validation/hook/${simulationId}?${params.toString()}`, {
      method: "POST",
    });
  },

  async getViolations(simulationId: string): Promise<ViolationRow[]> {
    const raw = await apiFetch<ApiViolationsResponse>(
      `/validation/hook/${simulationId}/violations`,
    );
    return fromApiViolations(raw);
  },

  async getEngineEvents(
    simulationId: string,
    options: { offset?: number; limit?: number } = {},
  ): Promise<EvEntry[]> {
    const raw = await apiFetch<ApiEventResponse>(
      `/simulations/${simulationId}/events`,
      { query: { limit: options.limit ?? 200, offset: options.offset ?? 0 } },
    );
    return fromApiEngineEvents(raw);
  },

  async forkFromSnapshot(snapshotId: string): Promise<ForkResult> {
    const raw = await apiFetch<ApiForkResponse>(
      `/snapshots/${snapshotId}/fork`,
      { method: "POST" },
    );
    return {
      simulationId: raw.simulation_id,
      runId: raw.run_id,
      currentRound: raw.current_round ?? 0,
      isComplete: !!raw.is_complete,
    };
  },
};
