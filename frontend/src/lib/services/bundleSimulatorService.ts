import { apiFetch } from "@/lib/api/client";

export interface BundleSimulatorBundle {
  txs: string[];
  tip_lamports: number;
  tip_recipient: string;
}

export interface TipOptimizerRequest {
  target_percentile: number;
}

export interface BundleForkSpecRequest {
  slot: number;
  protocols: {
    protocol_model: string;
    account_pubkey_allowlist?: string[] | null;
  }[];
  include_wallet_accounts?: string[] | null;
}

export interface BundleSimulatorRequest {
  bundle: BundleSimulatorBundle;
  context_slot: "latest" | number;
  fork_spec?: BundleForkSpecRequest | null;
  search_tip_optimizer?: TipOptimizerRequest | null;
}

export interface ProfitDistribution {
  p10?: number | null;
  p50: number;
  p75?: number | null;
  p90: number;
  p99?: number | null;
}

export interface AltCompression {
  uncompressed_bytes: number;
  compressed_bytes: number;
  used_alts?: string[];
}

export interface CuBudget {
  tx_cu_used: number[];
  slot_cu_headroom: number;
  slot_full?: boolean;
}

export interface WriteLockContention {
  blocking_pubkeys: string[];
  contended_lock_count?: number;
  relaxed_lock_count?: number;
}

export interface TipOptimizerResult {
  target_percentile: number;
  minimum_tip_lamports: number;
  safety_margin_lamports?: number;
  priority_fee_quote_lamports?: number;
}

export interface CalibrationThreshold {
  relative?: number | null;
  absolute?: number | null;
  supported?: boolean;
}

export interface CalibrationBlock {
  calibrated_at: string;
  corpus_slot?: number;
  metric_thresholds: Record<string, CalibrationThreshold>;
}

export interface MetricResult {
  value: number;
  unit: string;
  sample_size: number;
}

export interface LatencyDistribution extends MetricResult {
  mean: number;
  median: number;
  p95: number;
  p99: number;
  samples: number[];
}

export interface SimulateBundleMetrics {
  replay: {
    bundle_landing_rate: MetricResult;
    tip_efficiency: MetricResult;
    slot_inclusion_latency: LatencyDistribution;
  };
}

export interface BundleSimulatorResponse {
  expected_tip_to_land_lamports: number;
  landing_probability: number;
  profit_distribution: ProfitDistribution;
  alt_compression: AltCompression;
  cu_budget: CuBudget;
  write_lock_contention: WriteLockContention;
  tip_optimizer: TipOptimizerResult | null;
  calibration: CalibrationBlock | null;
  metrics: SimulateBundleMetrics;
}

export const bundleSimulatorService = {
  async simulate(
    body: BundleSimulatorRequest,
  ): Promise<BundleSimulatorResponse> {
    return apiFetch<BundleSimulatorResponse>("/v1/simulate-bundle", {
      method: "POST",
      body,
    });
  },
};
