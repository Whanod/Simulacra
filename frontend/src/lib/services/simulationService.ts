import type {
  EvEntry,
  RunSpec,
  SimRun,
} from "@/lib/types";
import { apiFetch } from "@/lib/api/client";
import {
  fromApiEvents,
  fromApiRun,
  fromApiRuns,
  specToApi,
  type ApiRun,
  type ApiRunEventsResponse,
  type ApiRunResult,
  type ApiRunSpec,
  type ApiRunsListResponse,
} from "@/lib/api/adapters/runs";
import {
  fromApiTemplates,
  type ApiTemplatesResponse,
  type SimTemplate,
} from "@/lib/api/adapters/templates";
import {
  fromApiCompare,
  type ApiCompareResponse,
  type CompareView,
} from "@/lib/api/adapters/compare";
import { apiFetchBlob } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { runViewService } from "@/lib/services/runViewService";

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

interface ApiValidationResponse {
  valid: boolean;
  errors: string[];
}

export interface Snapshot {
  id: string;
  runId: string;
  round: number;
  name: string;
  createdAt: string;
  parentSnapshotId?: string;
}

export interface BuildSpecOptions {
  mode?: "sync" | "interactive";
  // Bypass `specToApi` and POST this body verbatim. Set when the
  // caller already holds a backend-shaped spec (e.g., raw-mode editor
  // round-tripping through `draftToApiSpec`) so we don't run the
  // frontend → backend converter on a body that's already in backend
  // shape.
  prebuiltApiSpec?: Record<string, unknown>;
}

export interface ValidateSpecOptions {
  prebuiltApiSpec?: Record<string, unknown>;
}

export interface BuildSpecSyncResult {
  runId: string;
}

export interface BuildSpecInteractiveResult {
  runId: string;
  simulationId: string;
}

interface ApiSnapshot {
  snapshot_id: string;
  run_id: string;
  source_run_id?: string | null;
  simulation_id?: string | null;
  round: number;
  label?: string | null;
  created_at: string;
}

interface ApiSnapshotsResponse {
  run_id: string;
  snapshots: ApiSnapshot[];
}

interface ApiBuildResponse {
  simulation_id: string;
  run_id: string;
}

interface ApiShareRunResponse {
  run_id: string;
  run: ApiRun;
  permanent?: boolean;
  expires_at?: string | null;
  page_path?: string;
  page_url?: string;
  results_path?: string;
  results_url?: string;
  spec?: ApiRunSpec | null;
  result?: ApiRunResult | null;
}

export interface RunShareStatus {
  runId: string;
  permanent: boolean;
  expiresAt: string | null;
  pagePath: string;
  pageUrl: string;
  resultsPath: string;
  resultsUrl: string;
  walletOwner?: string;
}

export interface WalletPersistenceChallenge {
  runId: string;
  walletPubkey: string;
  nonce: string;
  message: string;
  expiresAt: string;
}

interface ApiWalletPersistenceChallenge {
  run_id: string;
  wallet_pubkey: string;
  nonce: string;
  message: string;
  expires_at: string;
}

interface ApiWalletArtifactPromotion {
  run_id: string;
  wallet_pubkey: string;
  permanent: boolean;
  expires_at: string | null;
  run: ApiRun;
}

interface ApiWalletArtifactsResponse {
  wallet_pubkey: string;
  artifacts: ApiRun[];
  count?: number;
  limit?: number;
  offset?: number;
}

export interface WalletArtifactList {
  walletPubkey: string;
  artifacts: SimRun[];
  count: number;
  limit: number;
  offset: number;
}

function snapshotFromApi(raw: ApiSnapshot): Snapshot {
  return {
    id: raw.snapshot_id,
    runId: raw.run_id,
    round: raw.round,
    name: raw.label ?? `round ${raw.round}`,
    createdAt: raw.created_at,
    parentSnapshotId: raw.source_run_id ?? undefined,
  };
}

function walletOwnerFromSummary(summary?: ApiRun["summary"]): string | undefined {
  const owner = summary?.wallet_owner;
  if (typeof owner === "string" && owner.length > 0) return owner;
  const persistence = summary?.wallet_persistence;
  if (persistence && typeof persistence === "object" && !Array.isArray(persistence)) {
    const nestedOwner = (persistence as Record<string, unknown>).owner;
    if (typeof nestedOwner === "string" && nestedOwner.length > 0) return nestedOwner;
  }
  return undefined;
}

function shareStatusFromApi(raw: ApiShareRunResponse): RunShareStatus {
  return {
    runId: raw.run_id,
    permanent: raw.permanent === true,
    expiresAt: raw.expires_at ?? null,
    pagePath: raw.page_path ?? `/r/${raw.run_id}`,
    pageUrl: raw.page_url ?? `/r/${raw.run_id}`,
    resultsPath: raw.results_path ?? `/results/${raw.run_id}`,
    resultsUrl: raw.results_url ?? `/results/${raw.run_id}`,
    walletOwner: walletOwnerFromSummary(raw.run?.summary),
  };
}

export interface AgentTimelineEntry {
  round: number;
  timestamp: number;
  epoch: number;
  balance: number;
  cumulativeVolume: number;
  realizedPnl: number;
}

interface ApiAgentTimelineResponse {
  run_id?: string;
  agent_id?: string;
  timeline: Array<{
    round?: number;
    timestamp?: number;
    epoch?: number;
    state?: {
      balances?: Record<string, number>;
      cumulative_volume?: number;
      realized_pnl?: number;
    };
  }>;
}

function balanceTotal(balances?: Record<string, number>): number {
  if (!balances) return 0;
  let sum = 0;
  for (const v of Object.values(balances)) {
    if (typeof v === "number") sum += v;
  }
  return sum;
}

export type ExportFormat = "csv" | "json" | "parquet";

export const simulationService = {
  async listRuns(): Promise<SimRun[]> {
    const resp = await apiFetch<ApiRunsListResponse>("/runs", { query: { limit: 100 } });
    return fromApiRuns(resp.runs || []);
  },

  async getRun(id: string): Promise<SimRun | undefined> {
    try {
      const raw = await apiFetch<ApiRun>(`/runs/${id}`);
      return fromApiRun(raw);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return undefined;
      throw err;
    }
  },

  async getSharedRunBundle(
    id: string,
  ): Promise<{ run: SimRun; result: ApiRunResult | null; spec: unknown }> {
    const raw = await apiFetch<ApiShareRunResponse>(`/share/runs/${id}`);
    const spec = raw.spec ?? raw.run.spec ?? null;
    return {
      run: fromApiRun({ ...raw.run, spec: spec ?? undefined }),
      result: raw.result ?? null,
      spec,
    };
  },

  async getRunShareStatus(id: string): Promise<RunShareStatus> {
    const raw = await apiFetch<ApiShareRunResponse>(`/share/runs/${id}`);
    return shareStatusFromApi(raw);
  },

  async createWalletPersistenceChallenge(
    runId: string,
    walletPubkey: string,
  ): Promise<WalletPersistenceChallenge> {
    const raw = await apiFetch<ApiWalletPersistenceChallenge>(
      `/wallet/artifacts/${encodeURIComponent(runId)}/challenge`,
      {
        method: "POST",
        body: { wallet_pubkey: walletPubkey },
      },
    );
    return {
      runId: raw.run_id,
      walletPubkey: raw.wallet_pubkey,
      nonce: raw.nonce,
      message: raw.message,
      expiresAt: raw.expires_at,
    };
  },

  async promoteWalletArtifact(
    runId: string,
    input: {
      walletPubkey: string;
      nonce: string;
      signature: string;
      encoding?: "base64" | "base58";
    },
  ): Promise<RunShareStatus> {
    const raw = await apiFetch<ApiWalletArtifactPromotion>(
      `/wallet/artifacts/${encodeURIComponent(runId)}/promote`,
      {
        method: "POST",
        body: {
          wallet_pubkey: input.walletPubkey,
          nonce: input.nonce,
          signature: input.signature,
          encoding: input.encoding ?? "base64",
        },
      },
    );
    return {
      runId: raw.run_id,
      permanent: raw.permanent === true,
      expiresAt: raw.expires_at ?? null,
      pagePath: `/r/${raw.run_id}`,
      pageUrl: `/r/${raw.run_id}`,
      resultsPath: `/results/${raw.run_id}`,
      resultsUrl: `/results/${raw.run_id}`,
      walletOwner: walletOwnerFromSummary(raw.run?.summary) ?? raw.wallet_pubkey,
    };
  },

  async listWalletArtifacts(
    walletPubkey: string,
    options: { limit?: number; offset?: number } = {},
  ): Promise<WalletArtifactList> {
    const limit = options.limit ?? 25;
    const offset = options.offset ?? 0;
    const raw = await apiFetch<ApiWalletArtifactsResponse>("/wallet/artifacts", {
      query: {
        wallet_pubkey: walletPubkey,
        limit,
        offset,
      },
    });
    return {
      walletPubkey: raw.wallet_pubkey,
      artifacts: fromApiRuns(raw.artifacts || []),
      count: raw.count ?? raw.artifacts?.length ?? 0,
      limit: raw.limit ?? limit,
      offset: raw.offset ?? offset,
    };
  },

  async getEvents(
    runId: string,
    options: { limit?: number; offset?: number; round?: number } = {},
  ): Promise<EvEntry[]> {
    const resp = await apiFetch<ApiRunEventsResponse>(`/runs/${runId}/events`, {
      query: {
        limit: options.limit ?? 500,
        offset: options.offset ?? 0,
        round: options.round,
      },
    });
    return fromApiEvents(resp.events || []);
  },

  async getSpec(runId: string): Promise<unknown> {
    const resp = await apiFetch<{ run_id: string; spec: unknown }>(
      `/runs/${runId}/spec`,
    );
    return resp.spec;
  },

  async getAgentTimeline(
    runId: string,
    agentId: string,
    options: { start?: number; end?: number; limit?: number; offset?: number } = {},
  ): Promise<AgentTimelineEntry[]> {
    const resp = await apiFetch<ApiAgentTimelineResponse>(
      `/runs/${runId}/agents/${encodeURIComponent(agentId)}/timeline`,
      {
        query: {
          start: options.start,
          end: options.end,
          limit: options.limit ?? 200,
          offset: options.offset ?? 0,
        },
      },
    );
    return (resp.timeline || []).map((entry) => ({
      round: entry.round ?? 0,
      timestamp: entry.timestamp ?? 0,
      epoch: entry.epoch ?? 0,
      balance: balanceTotal(entry.state?.balances),
      cumulativeVolume: entry.state?.cumulative_volume ?? 0,
      realizedPnl: entry.state?.realized_pnl ?? 0,
    }));
  },

  async exportResult(
    runId: string,
    format: ExportFormat,
    fields?: string[],
  ): Promise<Blob> {
    // Round snapshots are the export's payload — pulled from the per-round
    // table via the postgres-backed /rounds endpoint instead of the legacy
    // mega-result. The limit is generous enough to cover a single run's
    // snapshots in one shot; long runs would need follow-up pagination.
    const resp = await apiFetch<{
      run_id: string;
      snapshots: Array<Record<string, unknown>>;
    }>(`/runs/${runId}/rounds`, { query: { limit: 100000 } });
    const rows = resp.snapshots ?? [];

    // Fallback for snapshot-less runs: build a single denormalized row off
    // the overview bundle so derived metrics + agent end-states still ship
    // to the user. Mirrors the legacy ``[{ run_id, ...result }]`` shape
    // (one row of top-level fields) without dragging the result blob back.
    let data: Array<Record<string, unknown>>;
    if (rows.length > 0) {
      data = rows;
    } else {
      const overview = await runViewService.fetchOverview(runId);
      data = [
        {
          run_id: runId,
          status: overview.run?.status,
          seed: overview.run?.seed,
          market_type: overview.spec_summary?.market_type,
          num_rounds: overview.spec_summary?.num_rounds,
          num_rounds_executed: overview.num_rounds_executed,
          tiles: overview.tiles,
          price_history: overview.price_history,
          agent_final_states: overview.agent_final_states,
        },
      ];
    }
    return apiFetchBlob(`/export/${format}`, {
      method: "POST",
      body: { data, fields },
    });
  },

  async compareRuns(leftRunId: string, rightRunId: string): Promise<CompareView> {
    const raw = await apiFetch<ApiCompareResponse>("/runs/compare", {
      method: "POST",
      body: { left_run_id: leftRunId, right_run_id: rightRunId },
    });
    return fromApiCompare(raw);
  },

  async getSnapshots(runId: string): Promise<Snapshot[]> {
    const resp = await apiFetch<ApiSnapshotsResponse>(`/runs/${runId}/snapshots`);
    return (resp.snapshots || []).map(snapshotFromApi);
  },

  async createSnapshot(
    simulationId: string,
    _round: number,
    name: string,
  ): Promise<Snapshot> {
    const raw = await apiFetch<ApiSnapshot>(
      `/simulations/${simulationId}/snapshots`,
      { method: "POST", body: { label: name } },
    );
    return snapshotFromApi(raw);
  },

  async buildSpec(
    spec: RunSpec,
    options: BuildSpecOptions = {},
  ): Promise<BuildSpecSyncResult | BuildSpecInteractiveResult> {
    const body = options.prebuiltApiSpec ?? specToApi(spec);
    if (options.mode === "interactive") {
      const resp = await apiFetch<ApiBuildResponse>("/simulations/build", {
        method: "POST",
        body,
      });
      return { runId: resp.run_id, simulationId: resp.simulation_id };
    }
    const resp = await apiFetch<{ run_id: string }>("/simulations/run", {
      method: "POST",
      body,
    });
    return { runId: resp.run_id };
  },

  async validateSpec(
    spec: RunSpec,
    options: ValidateSpecOptions = {},
  ): Promise<ValidationResult> {
    const body = options.prebuiltApiSpec ?? specToApi(spec);
    const resp = await apiFetch<ApiValidationResponse>("/registry/validate", {
      method: "POST",
      body,
    });
    return { valid: !!resp.valid, errors: resp.errors ?? [] };
  },

  async getTemplates(): Promise<SimTemplate[]> {
    const resp = await apiFetch<ApiTemplatesResponse>("/templates/experiments");
    return fromApiTemplates(resp.templates || []);
  },
};

export function isInteractiveBuild(
  result: BuildSpecSyncResult | BuildSpecInteractiveResult,
): result is BuildSpecInteractiveResult {
  return "simulationId" in result;
}
