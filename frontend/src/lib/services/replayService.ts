import { apiFetch } from "@/lib/api/client";
import type { ApiRunResult } from "@/lib/api/adapters/runs";

export type ReplayCounterfactualKind =
  | "TipReplaceCounterfactual"
  | "FeeReplaceCounterfactual"
  | "OrderingReplaceCounterfactual"
  | "AgentInjectCounterfactual";

export interface ReplayCounterfactualSpec {
  kind: ReplayCounterfactualKind;
  params: Record<string, unknown>;
}

export interface TipPnlSweepPoint {
  tipLamports: number;
  pnlLamports: number;
  mainnetPnlLamports?: number | null;
  landingProbability?: number | null;
}

export interface ReplayErrorBand {
  metric: string;
  predicted: number;
  actual: number | null;
  abs_error?: number | null;
  rel_error?: number | null;
  absolute_error?: number | null;
  relative_error?: number | null;
  threshold?: number | null;
  threshold_kind?: "absolute" | "relative" | null;
  supported?: boolean;
}

export interface ReplayDiffPayload {
  per_metric_error?: Record<string, ReplayErrorBand>;
  unsupported_instruction_coverage?: number;
  [key: string]: unknown;
}

export interface ReplayRequest {
  slotStart: number;
  slotEnd: number;
  counterfactuals?: ReplayCounterfactualSpec[];
  seed?: number | null;
}

export interface ReplayResult {
  runId: string;
  slotRange: [number, number];
  slotsLoaded: number;
  counterfactuals: Array<Record<string, unknown>>;
  decodedTransactionShare: number;
  unsupportedProgramIds: string[];
  eligibleForCalibration: boolean;
  replayKind: string;
  mainnetAccuracyClaim: boolean;
  tipPnlSweep: TipPnlSweepPoint[];
  replayMetrics: unknown | null;
  replayDiff: ReplayDiffPayload | null;
  resultArtifact: ApiRunResult | null;
}

interface ApiReplayRequest {
  slot_range: [number, number];
  counterfactuals: ReplayCounterfactualSpec[];
  seed?: number | null;
}

interface ApiReplayResponse {
  run_id: string;
  slot_range: [number, number];
  slots_loaded: number;
  counterfactuals: Array<Record<string, unknown>>;
  decoded_transaction_share: number;
  unsupported_program_ids: string[];
  eligible_for_calibration: boolean;
  replay_kind?: string;
  mainnet_accuracy_claim?: boolean;
  tip_pnl_sweep?: ApiTipPnlSweepPoint[];
}

interface ApiTipPnlSweepPoint {
  tip_lamports?: unknown;
  pnl_lamports?: unknown;
  mainnet_pnl_lamports?: unknown;
  landing_probability?: unknown;
}

interface ApiRunResultResponse {
  run_id: string;
  result: ApiRunResult;
}

export interface ReplayBundleTarget {
  bundleId: string;
  tipLamports: number;
  numActions: number;
}

export interface ReplayPoolTarget {
  poolId: string;
  decodedSwaps: number;
}

export interface ReplayTargets {
  slot: number;
  bundles: ReplayBundleTarget[];
  pools: ReplayPoolTarget[];
}

interface ApiReplayTargetsResponse {
  slot: number;
  bundles: Array<{
    bundle_id: string;
    tip_lamports: number;
    num_actions: number;
  }>;
  pools: Array<{
    pool_id: string;
    decoded_swaps: number;
  }>;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function normalizeTipPnlSweep(
  raw: ApiTipPnlSweepPoint[] | undefined,
): TipPnlSweepPoint[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((point) => {
    if (!finiteNumber(point.tip_lamports) || !finiteNumber(point.pnl_lamports)) {
      return [];
    }
    return [
      {
        tipLamports: point.tip_lamports,
        pnlLamports: point.pnl_lamports,
        mainnetPnlLamports: finiteNumber(point.mainnet_pnl_lamports)
          ? point.mainnet_pnl_lamports
          : null,
        landingProbability: finiteNumber(point.landing_probability)
          ? point.landing_probability
          : null,
      },
    ];
  });
}

function latestReplayMetrics(result: ApiRunResult | null): unknown | null {
  const snapshots = result?.round_snapshots ?? [];
  for (let i = snapshots.length - 1; i >= 0; i--) {
    const metrics = snapshots[i]?.metrics;
    if (!metrics || typeof metrics !== "object") continue;
    const replay = (metrics as { replay?: unknown }).replay;
    if (replay && typeof replay === "object") return replay;
  }
  return null;
}

function normalizeReplayDiff(result: ApiRunResult | null): ReplayDiffPayload | null {
  const diff = result?.replay_diff;
  if (!diff || typeof diff !== "object" || Array.isArray(diff)) return null;
  return diff as ReplayDiffPayload;
}

export const replayService = {
  async getTargets(slot: number): Promise<ReplayTargets> {
    const raw = await apiFetch<ApiReplayTargetsResponse>(
      `/v1/replay/targets/${encodeURIComponent(String(slot))}`,
    );
    return {
      slot: raw.slot,
      bundles: raw.bundles.map((b) => ({
        bundleId: b.bundle_id,
        tipLamports: b.tip_lamports,
        numActions: b.num_actions,
      })),
      pools: raw.pools.map((p) => ({
        poolId: p.pool_id,
        decodedSwaps: p.decoded_swaps,
      })),
    };
  },

  async submitReplay(req: ReplayRequest): Promise<ReplayResult> {
    const body: ApiReplayRequest = {
      slot_range: [req.slotStart, req.slotEnd],
      counterfactuals: req.counterfactuals ?? [],
      seed: req.seed ?? null,
    };
    const raw = await apiFetch<ApiReplayResponse>("/v1/replay", {
      method: "POST",
      body,
    });
    const artifact = await apiFetch<ApiRunResultResponse>(
      `/runs/${encodeURIComponent(raw.run_id)}/result`,
    );
    const resultArtifact = artifact.result ?? null;
    return {
      runId: raw.run_id,
      slotRange: raw.slot_range,
      slotsLoaded: raw.slots_loaded,
      counterfactuals: raw.counterfactuals,
      decodedTransactionShare: raw.decoded_transaction_share,
      unsupportedProgramIds: raw.unsupported_program_ids,
      eligibleForCalibration: raw.eligible_for_calibration,
      replayKind: raw.replay_kind ?? "synthetic_or_partial_replay",
      mainnetAccuracyClaim: raw.mainnet_accuracy_claim ?? false,
      tipPnlSweep: normalizeTipPnlSweep(raw.tip_pnl_sweep),
      replayMetrics: latestReplayMetrics(resultArtifact),
      replayDiff: normalizeReplayDiff(resultArtifact),
      resultArtifact,
    };
  },
};
