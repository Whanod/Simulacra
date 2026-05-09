import type {
  AgentMix,
  AgentRow,
  EvEntry,
  MarketTokenSpec,
  RunSpec,
  SimMetrics,
  SimRun,
  SimStatus,
} from "@/lib/types/simulations";
import { hashEventClass } from "@/lib/utils/hashColor";

// ── Backend shapes ─────────────────────────────────────────────────────────

export interface ApiRunSummary {
  num_rounds?: number;
  num_rounds_executed?: number;
  agent_count?: number;
  event_count?: number;
  price_points?: number;
  available_rounds?: number[];
  stopped_early?: boolean;
  cancelled?: boolean;
  stop_reason?: string | null;
  price_summary?: Record<string, { start?: number; end?: number; delta?: number }>;
  agent_summary?: Record<
    string,
    { cumulative_volume?: number; realized_pnl?: number; balance_total?: number }
  >;
  final_agent_ids?: string[];
  [key: string]: unknown;
}

export interface ApiRunSpec {
  clock?: {
    type?: string;
    params?: Record<string, unknown>;
  };
  ordering?: {
    type?: string;
    params?: Record<string, unknown>;
  };
  gas_model?: {
    type?: string;
    params?: Record<string, unknown>;
  };
  execution?: {
    type?: string;
    params?: Record<string, unknown>;
    ordering?: {
      type?: string;
      params?: Record<string, unknown>;
    };
    gas_model?: {
      type?: string;
      params?: Record<string, unknown>;
    };
  };
  information_filter?: {
    type?: string;
    params?: Record<string, unknown>;
  };
  default_fee_model?: {
    type?: string;
    params?: Record<string, unknown>;
  };
  market?: {
    type?: string;
    tokens?: Array<{
      id?: string;
      symbol?: string;
      decimals?: number;
      native?: boolean;
      standard?: string;
      exchange_rate_to_sol?: number | string | null;
      exchange_rate_drift?: {
        drift_per_epoch?: number;
        volatility_per_epoch?: number;
        seed?: number | null;
      } | null;
      transfer_hook?: {
        program_id?: string | null;
        additional_cu_per_transfer?: number;
        additional_lamports_per_transfer?: number;
      } | null;
      confidential?: boolean;
    }>;
    params?: {
      initial_liquidity?: number;
      collateral_token?: string;
      corpus_slot?: number;
      pool_pubkey?: string;
      pool_account_id?: string;
      token_a_id?: string;
      token_b_id?: string;
      token_a_symbol?: string;
      token_b_symbol?: string;
    };
    fee_model?: {
      type?: string;
      params?: Record<string, unknown>;
    };
    markets?: Record<string, unknown>;
  };
  agents?: Array<{
    type?: string;
    agent_id?: string;
    params?: Record<string, unknown>;
    initial_balances?: Record<string, number>;
  }>;
  num_rounds?: number;
  snapshot_interval?: number;
  seed?: number;
  retain_snapshots?: boolean;
  numeric_mode?: string;
  feeds?: Array<{
    type?: string;
    params?: Record<string, unknown>;
    feeds?: Record<string, unknown>;
  }>;
  parameters?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ApiRun {
  run_id: string;
  simulation_id?: string | null;
  source?: string;
  source_run_id?: string | null;
  source_snapshot_id?: string | null;
  status?: string;
  seed?: number | null;
  market_type?: string | null;
  current_round?: number;
  created_at?: string;
  updated_at?: string;
  summary?: ApiRunSummary;
  spec?: ApiRunSpec;
  [key: string]: unknown;
}

export interface ApiRunsListResponse {
  runs: ApiRun[];
  count?: number;
  limit?: number;
  offset?: number;
}

export interface ApiRunResult {
  price_history?: Array<Record<string, number>>;
  agent_final_states?: Record<
    string,
    {
      agent_id?: string;
      role?: { name?: string; tags?: string[] };
      balances?: Record<string, number>;
      cumulative_volume?: number;
      cumulative_volume_quote?: number;
      realized_pnl?: number;
    }
  >;
  round_snapshots?: Array<Record<string, unknown>>;
  num_rounds?: number;
  num_rounds_executed?: number;
  volume_history?: number[];
  // Per-round fee totals, keyed by destination (e.g. `lp`, `protocol`, `burn`).
  // The backend emits one dict per executed round; destinations with zero
  // fees in a given round may be omitted. Values are typed `unknown`
  // because the backend encodes integers outside JS's safe range as
  // `{__defi_sim_bigint__: "<digits>"}` (see engine/json.py); the adapter
  // decodes both shapes via `toChartNumber`.
  fee_history?: Array<Record<string, unknown>>;
  liquidity_history?: number[];
  // Engine-computed end-of-run metrics (Simulation._compute_derived_metrics).
  // Surfaced under metadata.derived_metrics; backend may emit JSON `null`
  // when a metric is not applicable to the run. The engine appends
  // ``:agent_id`` suffixes for per-LP variants of range-aware metrics
  // (e.g. ``lp_in_range_fraction:lp-tight``), so this map is intentionally
  // open-ended.
  metadata?: {
    derived_metrics?: {
      kl_divergence?: number | null;
      convergence_speed?: number | null;
      manipulation_cost?: number | null;
      slippage?: number | null;
      exitability?: number | null;
      [key: string]: number | null | undefined;
    };
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ApiRunResultResponse {
  run_id: string;
  result: ApiRunResult;
}

export interface ApiRunEventsResponse {
  run_id: string;
  events: ApiEventRaw[];
}

export interface ApiEventRaw {
  event_id?: number;
  run_id?: string;
  type?: string;
  round?: number;
  timestamp?: number | string;
  data?: Record<string, unknown>;
}

// ── Status mapping ─────────────────────────────────────────────────────────

const STATUS_MAP: Record<string, SimStatus> = {
  completed: "completed",
  live: "running",
  running: "running",
  paused: "paused",
  cancelled: "cancelled",
  failed: "failed",
  error: "failed",
  ready: "completed",
  draft: "paused",
};

function mapStatus(raw: string | null | undefined): SimStatus {
  if (!raw) return "running";
  return STATUS_MAP[raw.toLowerCase()] ?? "running";
}

// ── Raw label extractors for UI columns (US-017) ───────────────────────────
//
// These pull the identifier strings the backend shipped directly out of
// the API response and feed them to the dashboard/results/compare UIs.
// No coercion, no pretty-casing, no hardcoded mapping — an unknown
// execution or fee model shows up in the UI verbatim rather than being
// dropped or remapped. Callers that want a "nicer" label should fetch
// it from the registry contract (RegistryEntityDefinition.label).

function marketLabelFromApi(
  type: string | null | undefined,
  spec?: ApiRunSpec,
): string {
  if (!type) return "";
  // The "world" market is a composite; when present, surface the
  // sub-market identifiers so the dashboard column stays informative.
  // The raw backend type is still the primary label otherwise.
  if (type.toLowerCase() === "world") {
    const names = spec?.market?.markets ? Object.keys(spec.market.markets) : [];
    return names.length > 0 ? `world (${names.join("+")})` : "world";
  }
  return type;
}

function execLabelFromApi(spec?: ApiRunSpec): string {
  const model = spec?.execution?.type?.toString();
  return model ?? "";
}

function orderingLabelFromApi(spec?: ApiRunSpec): string {
  const ordering =
    spec?.execution?.ordering?.type?.toString() ??
    spec?.ordering?.type?.toString();
  return ordering ?? "";
}

function feeLabelFromApi(spec?: ApiRunSpec): string {
  const fee = spec?.default_fee_model ?? spec?.market?.fee_model;
  if (!fee?.type) return "";
  const rate =
    typeof fee.params?.trade_fee_bps === "number"
      ? fee.params.trade_fee_bps
      : typeof fee.params?.base_bps === "number"
        ? fee.params.base_bps
        : undefined;
  return typeof rate === "number" ? `${fee.type} ${rate}bps` : fee.type;
}

function feedLabelFromApi(spec?: ApiRunSpec): string {
  const feeds = spec?.feeds;
  if (Array.isArray(feeds) && feeds.length > 0) {
    const first = feeds[0];
    const process =
      typeof first?.params?.process === "string" ? first.params.process : undefined;
    return process ?? first?.type ?? "";
  }
  return "";
}

function synthName(apiRun: ApiRun): string {
  const market = apiRun.market_type || "sim";
  const seed = apiRun.seed ?? "?";
  return `${market}-${seed}`;
}

// ── Spec mapping ───────────────────────────────────────────────────────────

function defaultMix(): AgentMix {
  return {
    noise: 0.4,
    informed: 0.2,
    arbitrageur: 0.15,
    manipulator: 0.05,
    passive_lp: 0.15,
    rebalancing_lp: 0.05,
  };
}

/**
 * Build the structured `agents.groups` list from a backend agent
 * array. Coalesces neighbouring agents with identical (type, params,
 * initial_balances, agent_id stem) into a single group with `count`
 * set, so the lighthouse template's 4 noise + 1 victim-1 +
 * 1 victim-small + 1 sandwich-1 + 1 lp-1 + 1 searcher-1 round-trips
 * verbatim instead of collapsing to one-per-type.
 */
function groupsFromAgents(
  agents: ApiRunSpec["agents"],
): import("@/lib/types/simulations").AgentGroup[] | undefined {
  if (!Array.isArray(agents) || agents.length === 0) return undefined;
  const out: import("@/lib/types/simulations").AgentGroup[] = [];
  // Track first-seen agent_id per group so we can preserve the full
  // id when count === 1 (e.g. "victim-small") but strip the trailing
  // `-N` numeric suffix when count > 1 (e.g. four noise-1..noise-4
  // agents collapse to prefix "noise").
  const firstId = new Map<string, string | undefined>();
  for (const a of agents) {
    const t = typeof a?.type === "string" ? a.type : undefined;
    if (!t) continue;
    const params = (a.params ?? {}) as Record<string, unknown>;
    const balances = (a.initial_balances ?? {}) as Record<string, number>;
    const agentId = typeof a.agent_id === "string" ? a.agent_id : undefined;
    const existing = out.find(
      (g) =>
        g.type === t &&
        JSON.stringify(g.params) === JSON.stringify(params) &&
        JSON.stringify(g.initialBalances ?? {}) === JSON.stringify(balances),
    );
    if (existing) {
      existing.count = (existing.count ?? 1) + 1;
    } else {
      const id = `g-${t}-${out.length}`;
      out.push({
        id,
        type: t,
        weight: 0,
        count: 1,
        params,
        initialBalances: balances,
        agentIdPrefix: agentId,
      });
      firstId.set(id, agentId);
    }
  }
  for (const g of out) {
    if ((g.count ?? 1) > 1) {
      const first = firstId.get(g.id);
      if (typeof first === "string") {
        const stripped = first.replace(/-\d+$/, "");
        g.agentIdPrefix = stripped.length > 0 ? stripped : undefined;
      }
    }
  }
  // Mirror count → weight as a percentage share (sum ≈ 100%) so the
  // builder's slider and weight-sum summary read naturally. specToApi
  // still prefers count when present, so populations round-trip
  // exactly until the user touches the slider (which clears count
  // and lets the percentage drive distribution).
  const totalCount = out.reduce((s, g) => s + (g.count ?? 1), 0);
  if (totalCount > 0) {
    for (const g of out) {
      if (typeof g.count === "number") {
        g.weight = Math.round((g.count / totalCount) * 100);
      }
    }
  }
  return out.length > 0 ? out : undefined;
}

function mixFromAgents(agents: ApiRunSpec["agents"]): AgentMix {
  if (!Array.isArray(agents) || agents.length === 0) return defaultMix();
  const counts: Record<keyof AgentMix, number> = {
    noise: 0,
    informed: 0,
    arbitrageur: 0,
    manipulator: 0,
    passive_lp: 0,
    rebalancing_lp: 0,
  };
  for (const agent of agents) {
    const t = (agent?.type || "").toLowerCase();
    if (t in counts) counts[t as keyof AgentMix] += 1;
  }
  const total = agents.length;
  return {
    noise: counts.noise / total,
    informed: counts.informed / total,
    arbitrageur: counts.arbitrageur / total,
    manipulator: counts.manipulator / total,
    passive_lp: counts.passive_lp / total,
    rebalancing_lp: counts.rebalancing_lp / total,
  };
}

function readNumber(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function clockTypeFromApi(raw: string | undefined): RunSpec["clock"]["type"] {
  if (raw === "variable_block") return "variable";
  if (raw === "solana_slot") return "solana_slot";
  return "block";
}

function schedulerFromApi(
  raw: unknown,
): { scheduler: NonNullable<RunSpec["execution"]["scheduler"]> } | object {
  if (typeof raw === "string" && (raw === "serial" || raw === "priority")) {
    return { scheduler: raw };
  }
  if (raw && typeof raw === "object") {
    const t = (raw as Record<string, unknown>).type;
    if (t === "serial" || t === "priority") return { scheduler: t };
  }
  return {};
}

function oraclePresetFromApi(
  raw: unknown,
): { oracle_preset: NonNullable<RunSpec["execution"]["oracle_preset"]> } | object {
  if (
    raw === "pyth_pull" ||
    raw === "pyth_lazer" ||
    raw === "switchboard_on_demand"
  ) {
    return { oracle_preset: raw };
  }
  return {};
}

function computeBudgetFromApi(
  raw: unknown,
): { compute_budget: NonNullable<RunSpec["execution"]["compute_budget"]> } | object {
  if (!raw || typeof raw !== "object") return {};
  const cb = raw as Record<string, unknown>;
  return {
    compute_budget: {
      preset: "custom",
      per_slot: readNumber(cb.per_slot, 60_000_000),
      per_tx: readNumber(cb.per_tx, 1_400_000),
      per_writable_account: readNumber(cb.per_writable_account, 12_000_000),
    },
  };
}

function priorityFeeMarketFromApi(
  raw: unknown,
):
  | { priority_fee_market: NonNullable<RunSpec["execution"]["priority_fee_market"]> }
  | object {
  if (!raw || typeof raw !== "object") return {};
  const pfm = raw as Record<string, unknown>;
  const out: NonNullable<RunSpec["execution"]["priority_fee_market"]> = {
    window_slots: readNumber(pfm.window_slots, 150),
    ewma_half_life_slots: readNumber(pfm.ewma_half_life_slots, 30),
    floor_micro_lamports: readNumber(pfm.floor_micro_lamports, 1),
    update_event_threshold: readNumber(pfm.update_event_threshold, 0.05),
  };
  if (pfm.pre_roll && typeof pfm.pre_roll === "object") {
    const pr = pfm.pre_roll as Record<string, unknown>;
    out.pre_roll = {
      slots: readNumber(pr.slots, 0),
      accounts: Array.isArray(pr.accounts)
        ? (pr.accounts as unknown[]).filter(
            (a): a is string => typeof a === "string",
          )
        : [],
      cu_price_min: readNumber(pr.cu_price_min, 1_000),
      cu_price_max: readNumber(pr.cu_price_max, 50_000),
      observations_per_slot: readNumber(pr.observations_per_slot, 1),
      seed: readNumber(pr.seed, 0),
    };
  }
  return { priority_fee_market: out };
}

function bundleAuctionFromApi(
  raw: unknown,
):
  | { bundle_auction: NonNullable<RunSpec["execution"]["bundle_auction"]> }
  | object {
  if (!raw || typeof raw !== "object") return {};
  const b = raw as Record<string, unknown>;
  const out: NonNullable<RunSpec["execution"]["bundle_auction"]> = {
    max_bundles_per_slot: readNumber(b.max_bundles_per_slot, 5),
    jito_stake_pool_share: readNumber(b.jito_stake_pool_share, 0.05),
  };
  if (typeof b.tip_quote_curve_path === "string" && b.tip_quote_curve_path) {
    out.tip_quote_curve_path = b.tip_quote_curve_path;
  }
  return { bundle_auction: out };
}

function altsFromApi(
  raw: unknown,
): NonNullable<RunSpec["alts"]> | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: NonNullable<RunSpec["alts"]> = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const r = item as Record<string, unknown>;
    if (typeof r.id !== "string") continue;
    const entries = Array.isArray(r.entries)
      ? (r.entries as unknown[]).filter(
          (a): a is string => typeof a === "string",
        )
      : [];
    out.push({ id: r.id, entries });
  }
  return out.length > 0 ? out : undefined;
}

function forkSpecFromApi(
  raw: unknown,
):
  | { fork_spec: NonNullable<RunSpec["execution"]["fork_spec"]> }
  | object {
  if (!raw || typeof raw !== "object") return {};
  const fs = raw as Record<string, unknown>;
  const seed =
    typeof fs.seed === "number"
      ? fs.seed
      : fs.seed === null
        ? null
        : undefined;
  return {
    fork_spec: {
      fork_probability_per_slot: readNumber(fs.fork_probability_per_slot, 0),
      max_reorg_depth_slots: readNumber(fs.max_reorg_depth_slots, 5),
      ...(seed !== undefined ? { seed } : {}),
    },
  };
}

function clockFromApi(raw: ApiRunSpec["clock"]): RunSpec["clock"] {
  const type = clockTypeFromApi(raw?.type);
  if (type === "solana_slot") {
    return {
      type,
      block_time: readNumber(raw?.params?.slot_duration_seconds, 0.4),
      epoch_length: readNumber(raw?.params?.epoch_length_slots, 432_000),
      skip_rate: readNumber(raw?.params?.skip_rate, 0),
    };
  }
  return {
    type,
    block_time: readNumber(raw?.params?.block_time, 1),
    epoch_length: readNumber(raw?.params?.epoch_length, 1),
  };
}

/**
 * Extracts the first feed from an api spec (US-017: inlined from the
 * retired `feedFromApi` helper). Unknown feed types pass through
 * verbatim; only the canonical `stochastic` feed gets its sub-process
 * surfaced as `type` for builder ergonomics. Everything else keeps its
 * backend identifier, so new feed types land without a frontend edit.
 */
function firstFeedFromApi(raw: ApiRunSpec["feeds"]): RunSpec["feeds"][number] {
  const first = Array.isArray(raw) && raw.length > 0 ? raw[0] : undefined;
  const params = first?.params ?? {};
  const processParams =
    typeof params.process_params === "object" && params.process_params !== null
      ? (params.process_params as Record<string, unknown>)
      : {};
  const initialPrice = readNumber(processParams.initial, 1.0);
  const drift = readNumber(processParams.mu, 0.0001);
  const volatility = readNumber(processParams.sigma, 0.02);
  const process = typeof params.process === "string" ? params.process : "gbm";

  if (!first) {
    return {
      type: "stochastic",
      process: "gbm",
      drift,
      volatility,
      initial_price: initialPrice,
    };
  }

  const backendType = typeof first.type === "string" ? first.type : "stochastic";

  if (backendType === "stochastic") {
    if (process === "mean_reversion") {
      return { type: "mean_revert", process, drift, volatility, initial_price: initialPrice };
    }
    if (process === "jump_diffusion") {
      return { type: "jump", process, drift, volatility, initial_price: initialPrice };
    }
  }

  return {
    type: backendType,
    process,
    drift,
    volatility,
    initial_price: initialPrice,
  };
}

type ApiSpecTokens = NonNullable<NonNullable<ApiRunSpec["market"]>["tokens"]>;

function normalizeMarketTokens(
  raw: ApiSpecTokens | undefined,
): MarketTokenSpec[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: MarketTokenSpec[] = [];
  for (const t of raw) {
    const id = typeof t?.id === "string" ? t.id : t?.symbol;
    const symbol = typeof t?.symbol === "string" ? t.symbol : id;
    if (typeof id !== "string" || typeof symbol !== "string") continue;
    const decimals = typeof t?.decimals === "number" ? t.decimals : 9;
    const standard =
      t?.standard === "native" || t?.standard === "spl" || t?.standard === "spl_2022"
        ? t.standard
        : undefined;
    const native = typeof t?.native === "boolean" ? t.native : undefined;
    const entry: MarketTokenSpec = { id, symbol, decimals };
    if (native !== undefined) entry.native = native;
    if (standard !== undefined) entry.standard = standard;

    // US-007 extension fields: pass through verbatim when present.
    if (t?.exchange_rate_to_sol !== undefined && t.exchange_rate_to_sol !== null) {
      entry.exchange_rate_to_sol = t.exchange_rate_to_sol;
    }
    const drift = t?.exchange_rate_drift;
    if (drift && typeof drift === "object") {
      entry.exchange_rate_drift = {
        drift_per_epoch:
          typeof drift.drift_per_epoch === "number" ? drift.drift_per_epoch : 0.0001,
        volatility_per_epoch:
          typeof drift.volatility_per_epoch === "number" ? drift.volatility_per_epoch : 0,
        ...(typeof drift.seed === "number" ? { seed: drift.seed } : {}),
      };
    }
    const hook = t?.transfer_hook;
    if (hook && typeof hook === "object") {
      entry.transfer_hook = {
        ...(typeof hook.program_id === "string" ? { program_id: hook.program_id } : {}),
        additional_cu_per_transfer:
          typeof hook.additional_cu_per_transfer === "number"
            ? hook.additional_cu_per_transfer
            : 0,
        additional_lamports_per_transfer:
          typeof hook.additional_lamports_per_transfer === "number"
            ? hook.additional_lamports_per_transfer
            : 0,
      };
    }
    if (typeof t?.confidential === "boolean") entry.confidential = t.confidential;

    out.push(entry);
  }
  return out.length > 0 ? out : undefined;
}

export function specFromApi(raw: ApiRunSpec | undefined): RunSpec {
  const marketType = (raw?.market?.type || "cfamm") as RunSpec["market"]["type"];
  const tokens = raw?.market?.tokens || [];
  const initialLiquidity =
    typeof raw?.market?.params?.initial_liquidity === "number"
      ? raw.market.params.initial_liquidity
      : 1_000_000;
  const decimals =
    tokens[0]?.decimals !== undefined && typeof tokens[0].decimals === "number"
      ? tokens[0].decimals
      : 9;

  const normalizedTokens = normalizeMarketTokens(raw?.market?.tokens);
  const collateralTokenId =
    typeof raw?.market?.params?.collateral_token === "string"
      ? raw.market.params.collateral_token
      : undefined;

  const rawMarketParams = raw?.market?.params;
  const whirlpoolParams =
    marketType === "whirlpool" && rawMarketParams
      ? {
          ...(typeof rawMarketParams.corpus_slot === "number"
            ? { corpus_slot: rawMarketParams.corpus_slot }
            : {}),
          ...(typeof rawMarketParams.pool_pubkey === "string"
            ? { pool_pubkey: rawMarketParams.pool_pubkey }
            : {}),
          ...(typeof rawMarketParams.pool_account_id === "string"
            ? { pool_account_id: rawMarketParams.pool_account_id }
            : {}),
          ...(typeof rawMarketParams.token_a_id === "string"
            ? { token_a_id: rawMarketParams.token_a_id }
            : {}),
          ...(typeof rawMarketParams.token_b_id === "string"
            ? { token_b_id: rawMarketParams.token_b_id }
            : {}),
          ...(typeof rawMarketParams.token_a_symbol === "string"
            ? { token_a_symbol: rawMarketParams.token_a_symbol }
            : {}),
          ...(typeof rawMarketParams.token_b_symbol === "string"
            ? { token_b_symbol: rawMarketParams.token_b_symbol }
            : {}),
        }
      : undefined;

  return {
    market: {
      type: marketType,
      num_assets: Math.max(tokens.length, 2),
      initial_liquidity: initialLiquidity,
      token_decimals: decimals,
      ...(normalizedTokens ? { tokens: normalizedTokens } : {}),
      ...(collateralTokenId ? { collateral_token_id: collateralTokenId } : {}),
      ...(whirlpoolParams && Object.keys(whirlpoolParams).length > 0
        ? { whirlpool_params: whirlpoolParams }
        : {}),
    },
    clock: clockFromApi(raw?.clock),
    execution: {
      // US-017: pass the backend execution model through verbatim.
      // The RunSpec type is open (US-003) and the UI uses
      // RegistrySelect, which accepts any backend string.
      // Normalize the wire-only `solana_like` alias to the frontend
      // canonical `solana` so RunSpec consumers (executionToApi gates,
      // builder bExec state) can round-trip solana-only params without
      // an extra translation step.
      model: ((raw?.execution?.type === "solana_like"
        ? "solana"
        : raw?.execution?.type) || "direct") as RunSpec["execution"]["model"],
      ordering:
        ((raw?.execution?.ordering?.type || raw?.ordering?.type || "fifo") as RunSpec["execution"]["ordering"]),
      cost_model:
        ((raw?.execution?.gas_model?.type || raw?.gas_model?.type || "zero") as RunSpec["execution"]["cost_model"]),
      ...schedulerFromApi(raw?.execution?.params?.scheduler),
      ...computeBudgetFromApi(raw?.execution?.params?.compute_budget),
      ...oraclePresetFromApi(raw?.execution?.params?.oracle_preset),
      ...priorityFeeMarketFromApi(raw?.execution?.params?.priority_fee_market),
      ...bundleAuctionFromApi(raw?.execution?.params?.bundle_auction),
      ...forkSpecFromApi(raw?.execution?.params?.fork_spec),
      ...(typeof raw?.execution?.params?.cost_token === "string"
        ? { cost_token: raw.execution.params.cost_token as string }
        : {}),
      ...(Array.isArray(raw?.execution?.params?.visible_roles)
        ? {
            visible_roles: (raw.execution.params.visible_roles as unknown[])
              .filter((s): s is string => typeof s === "string"),
          }
        : {}),
    },
    fee_model: {
      type: ((raw?.default_fee_model?.type || raw?.market?.fee_model?.type || "flat") as RunSpec["fee_model"]["type"]),
      rate_bps: readNumber(
        raw?.default_fee_model?.params?.trade_fee_bps ??
          raw?.default_fee_model?.params?.base_bps ??
          raw?.market?.fee_model?.params?.trade_fee_bps ??
          raw?.market?.fee_model?.params?.base_bps,
        30,
      ),
    },
    agents: {
      total: raw?.agents?.length ?? 0,
      mix: mixFromAgents(raw?.agents),
      default_collateral: 100_000,
      ...(() => {
        const g = groupsFromAgents(raw?.agents);
        return g ? { groups: g } : {};
      })(),
    },
    feeds: [firstFeedFromApi(raw?.feeds)],
    config: {
      num_rounds: raw?.num_rounds ?? 0,
      snapshot_interval: raw?.snapshot_interval ?? 1,
      seed: raw?.seed ?? 0,
      numeric_mode: raw?.numeric_mode === "float" ? "FLOAT_MODE" : "FIXED_POINT",
      // US-017: information filter passes through verbatim; the
      // legacy identity/default helper was a no-op wrapper.
      information_filter: raw?.information_filter?.type || "full_transparency",
    },
    ...(() => {
      const alts = altsFromApi((raw as Record<string, unknown> | undefined)?.alts);
      return alts ? { alts } : {};
    })(),
  };
}

function roleParamsToApi(
  role: keyof AgentMix,
  overrides: RunSpec["agents"]["role_params"],
  collateralTokenId: string = "COLLATERAL",
): Record<string, unknown> {
  // Each backend agent dataclass accepts a *different* set of fields —
  // forwarding the whole UI RoleParams shape to every role breaks on noise
  // (no `conviction`), on arbitrageur (no `attack_capital`), etc.
  const base: Record<string, unknown> = { collateral: collateralTokenId };
  const rp = overrides?.[role];
  switch (role) {
    case "noise": {
      base.frequency = rp?.frequency ?? 0.5;
      if (typeof rp?.tradeMin === "number") base.trade_min = rp.tradeMin;
      if (typeof rp?.tradeMax === "number") base.trade_max = rp.tradeMax;
      return base;
    }
    case "informed": {
      if (typeof rp?.conviction === "number") base.conviction = rp.conviction;
      return base;
    }
    case "arbitrageur": {
      if (typeof rp?.priceTolerance === "number") {
        base.min_edge_bps = Math.round(rp.priceTolerance * 10_000);
      }
      return base;
    }
    case "manipulator": {
      if (typeof rp?.attackCapital === "number") {
        base.budget = rp.attackCapital;
      }
      return base;
    }
    case "passive_lp":
    case "rebalancing_lp": {
      if (typeof rp?.depositFraction === "number") {
        base.deposit_fraction = rp.depositFraction;
      }
      if (typeof rp?.rebalanceInterval === "number") {
        base.rebalance_interval = rp.rebalanceInterval;
      }
      return base;
    }
  }
  return base;
}

function worldMarketToApi(
  block: NonNullable<RunSpec["world"]>["markets"][number],
  decimals: number,
  initialLiquidity: number,
): Record<string, unknown> {
  if (block.type === "clob") {
    const base = block.tokens[0] || "BASE";
    const quote = block.tokens[1] || "QUOTE";
    return {
      type: "clob",
      pairs: [
        {
          base: { id: base, symbol: base, decimals },
          quote: { id: quote, symbol: quote, decimals },
        },
      ],
    };
  }
  const tokens = (block.tokens.length >= 2 ? block.tokens : ["YES", "NO"]).map(
    (sym) => ({ id: sym, symbol: sym, decimals }),
  );
  return {
    type: "cfamm",
    tokens,
    params: {
      initial_liquidity: initialLiquidity,
      collateral_token: "COLLATERAL",
    },
  };
}

function feeModelToApi(spec: RunSpec["fee_model"]): Record<string, unknown> {
  switch (spec.type) {
    case "flat":
      return {
        type: "flat",
        params: { trade_fee_bps: spec.rate_bps },
      };
    case "dynamic":
      return {
        type: "dynamic",
        params: {
          base_bps: spec.rate_bps,
          max_bps: Math.max(spec.rate_bps + 1, spec.rate_bps * 3),
          volatility_multiplier: 2.0,
        },
      };
    case "tiered":
      return {
        type: "tiered",
        params: { base_bps: spec.rate_bps },
      };
    case "spread":
      return {
        type: "spread",
        params: {
          base_bps: spec.rate_bps,
          spread_multiplier: 1.5,
        },
      };
    case "time_weighted":
      return {
        type: "time_weighted",
        params: {
          base_bps: spec.rate_bps,
          max_bps: Math.max(spec.rate_bps + 1, spec.rate_bps * 2),
        },
      };
    default:
      // Preserve an unknown backend fee type on the write side. We still
      // only know about rate_bps as a shared param — anything else the
      // backend needs will have to ride on the raw block once US-005
      // lands. For now this is enough to round-trip the fee type itself.
      return {
        type: spec.type,
        params: { base_bps: spec.rate_bps },
      };
  }
}

function clockToApi(spec: RunSpec): Record<string, unknown> {
  if (spec.clock.type === "variable") {
    const step = Math.max(1, spec.clock.block_time);
    const count = Math.max(spec.config.num_rounds, 1);
    return {
      type: "variable_block",
      params: {
        timestamps: Array.from({ length: count }, (_, i) => (i + 1) * step),
        epoch_length: spec.clock.epoch_length,
      },
    };
  }
  if (spec.clock.type === "solana_slot") {
    return {
      type: "solana_slot",
      params: {
        slot_duration_seconds: spec.clock.block_time,
        epoch_length_slots: spec.clock.epoch_length,
        skip_rate: spec.clock.skip_rate ?? 0,
      },
    };
  }
  return {
    type: "block",
    params: {
      block_time: spec.clock.block_time,
      epoch_length: spec.clock.epoch_length,
    },
  };
}

function gasModelToApi(model: RunSpec["execution"]["cost_model"]): Record<string, unknown> {
  switch (model) {
    case "fixed":
      return { type: "fixed", params: { cost_per_action: 1 } };
    case "typed":
      return { type: "typed", params: { costs: {}, default_cost: 1 } };
    case "eip1559":
      return {
        type: "eip1559",
        params: { base_fee: 1, target_actions_per_round: 50, adjustment_factor: 8 },
      };
    case "compute_unit":
      return { type: "compute_unit", params: {} };
    default:
      return { type: "zero", params: {} };
  }
}

function orderingToApi(ordering: RunSpec["execution"]["ordering"]): Record<string, unknown> {
  if (ordering === "block_builder") {
    throw new Error('Ordering model "block_builder" is not supported by the current backend.');
  }
  return {
    type: ordering,
    params:
      ordering === "sandwich"
        ? { adversarial_agent_ids: [], target_agent_ids: [] }
        : {},
  };
}

function executionToApi(spec: RunSpec): Record<string, unknown> {
  const type =
    spec.execution.model === "solana"
      ? "solana_like"
      : spec.execution.model;
  const params: Record<string, unknown> = {};
  // US-002: forward compute_budget on solana execution. Always include
  // when the spec carries the field, so a fully-default builder still
  // round-trips the per-tx/per-slot/per-account values to the backend.
  if (spec.execution.model === "solana" && spec.execution.compute_budget) {
    const cb = spec.execution.compute_budget;
    params.compute_budget = {
      per_slot: cb.per_slot,
      per_tx: cb.per_tx,
      per_writable_account: cb.per_writable_account,
    };
  }
  // US-003: forward scheduler discriminator on solana execution.
  if (spec.execution.model === "solana" && spec.execution.scheduler) {
    params.scheduler = spec.execution.scheduler;
  }
  // US-006: forward oracle preset on solana execution. `none` is the
  // default and is omitted; named presets round-trip verbatim so the
  // backend can resolve them against `defi_sim.engine.oracles.presets`.
  if (
    spec.execution.model === "solana" &&
    spec.execution.oracle_preset &&
    spec.execution.oracle_preset !== "none"
  ) {
    params.oracle_preset = spec.execution.oracle_preset;
  }
  // US-010 PRD line 747: forward the priority-fee market spec on solana
  // execution. Backend resolves via `PriorityFeeMarketSpec.from_dict` in
  // `_build_solana_like_execution`.
  if (spec.execution.model === "solana" && spec.execution.priority_fee_market) {
    const pfm = spec.execution.priority_fee_market;
    const out: Record<string, unknown> = {
      window_slots: pfm.window_slots,
      ewma_half_life_slots: pfm.ewma_half_life_slots,
      floor_micro_lamports: pfm.floor_micro_lamports,
      update_event_threshold: pfm.update_event_threshold,
    };
    if (pfm.pre_roll) {
      out.pre_roll = {
        slots: pfm.pre_roll.slots,
        accounts: pfm.pre_roll.accounts,
        cu_price_min: pfm.pre_roll.cu_price_min,
        cu_price_max: pfm.pre_roll.cu_price_max,
        observations_per_slot: pfm.pre_roll.observations_per_slot,
        seed: pfm.pre_roll.seed,
      };
    }
    params.priority_fee_market = out;
  }
  // Lighthouse: forward the bundle_auction config so JitoSearcher
  // bundles compete with realistic cohort tip-quote priors. Backend
  // resolves via `BundleAuctionParams.from_dict`.
  if (spec.execution.model === "solana" && spec.execution.bundle_auction) {
    const ba = spec.execution.bundle_auction;
    const out: Record<string, unknown> = {
      max_bundles_per_slot: ba.max_bundles_per_slot,
      jito_stake_pool_share: ba.jito_stake_pool_share,
    };
    if (ba.tip_quote_curve_path) out.tip_quote_curve_path = ba.tip_quote_curve_path;
    params.bundle_auction = out;
  }
  if (spec.execution.model === "solana" && spec.execution.cost_token) {
    params.cost_token = spec.execution.cost_token;
  }
  if (
    spec.execution.model === "solana" &&
    spec.execution.visible_roles &&
    spec.execution.visible_roles.length > 0
  ) {
    params.visible_roles = spec.execution.visible_roles;
  }
  // US-014 PRD line 1109 / 1125: forward the adversarial fork spec on
  // solana execution. Backend resolves via `ForkSpec(**fork_param)` in
  // `_build_solana_like_execution`.
  if (spec.execution.model === "solana" && spec.execution.fork_spec) {
    const fs = spec.execution.fork_spec;
    const forkOut: Record<string, unknown> = {
      fork_probability_per_slot: fs.fork_probability_per_slot,
      max_reorg_depth_slots: fs.max_reorg_depth_slots,
    };
    if (typeof fs.seed === "number") forkOut.seed = fs.seed;
    params.fork_spec = forkOut;
  }
  // US-012 PRD line 974: forward the builder's Validator Set so the
  // backend (build_engine) can synthesize Validator agent specs from
  // each entry. Backend resolves via `_expand_validator_set_into_agents`.
  if (
    spec.execution.model === "solana" &&
    spec.execution.validator_set &&
    spec.execution.validator_set.length > 0
  ) {
    params.validator_set = spec.execution.validator_set.map((v) => ({
      pubkey: v.pubkey,
      client: v.client,
      stake_lamports: v.stake_lamports,
      stake_pool_share: v.stake_pool_share,
      stake_pool_address: v.stake_pool_address ?? null,
      commission_pct: v.commission_pct ?? 0.05,
    }));
  }
  return {
    type,
    params,
    ordering: orderingToApi(spec.execution.ordering),
    gas_model: gasModelToApi(spec.execution.cost_model),
  };
}

function informationFilterToApi(spec: RunSpec): Record<string, unknown> {
  const filter = spec.config.information_filter;
  if (filter === "delayed" || filter === "delayed_information") {
    return {
      type: "delayed_information",
      params: {
        delays: {
          noise: 1,
          informed: 1,
          arbitrageur: 1,
          manipulator: 1,
          passive_lp: 1,
          rebalancing_lp: 1,
          lp: 1,
        },
      },
    };
  }
  if (!filter || filter === "full" || filter === "full_transparency") {
    return { type: "full_transparency", params: {} };
  }
  // Preserve an unknown backend filter type. Params will be filled in by
  // the draft round-trip once US-005 lands.
  return { type: filter, params: {} };
}

function feedToApi(
  spec: RunSpec["feeds"][number],
  seed: number,
): Record<string, unknown> {
  if (spec.type === "historical") {
    throw new Error('Historical feeds need explicit price series and are not wired up in the builder yet.');
  }
  if (spec.type === "composite") {
    throw new Error('Composite feeds need per-token sources and are not wired up in the builder yet.');
  }

  const isStochasticFamily =
    spec.type === "stochastic" || spec.type === "mean_revert" || spec.type === "jump";

  if (isStochasticFamily) {
    const process =
      spec.type === "mean_revert"
        ? "mean_reversion"
        : spec.type === "jump"
          ? "jump_diffusion"
          : "gbm";
    return {
      type: "stochastic",
      params: {
        process,
        process_params: {
          mu: spec.drift,
          sigma: spec.volatility,
          initial: spec.initial_price,
          ...(process === "mean_reversion" ? { theta: spec.initial_price, kappa: 0.1 } : {}),
        },
        seed,
      },
    };
  }

  // Unknown backend feed type: preserve type + process verbatim. Params
  // beyond the shared stochastic params will need the draft raw-block
  // once US-005 lands.
  return {
    type: spec.type,
    params: {
      process: spec.process,
      process_params: {
        mu: spec.drift,
        sigma: spec.volatility,
        initial: spec.initial_price,
      },
      seed,
    },
  };
}

export function specToApi(spec: RunSpec): Record<string, unknown> {
  const marketType = spec.market.type;
  const preservedTokens = spec.market.tokens;
  const tokens: Array<Record<string, unknown>> =
    preservedTokens && preservedTokens.length > 0
      ? preservedTokens.map((t) => {
          const out: Record<string, unknown> = {
            id: t.id,
            symbol: t.symbol,
            decimals: t.decimals,
          };
          if (t.native !== undefined) out.native = t.native;
          if (t.standard !== undefined) out.standard = t.standard;
          // US-007 extension fields. Emit only when present so legacy
          // payloads stay byte-for-byte identical.
          if (t.exchange_rate_to_sol !== undefined && t.exchange_rate_to_sol !== null) {
            out.exchange_rate_to_sol = t.exchange_rate_to_sol;
          }
          if (t.exchange_rate_drift) {
            const drift: Record<string, unknown> = {
              drift_per_epoch: t.exchange_rate_drift.drift_per_epoch,
              volatility_per_epoch: t.exchange_rate_drift.volatility_per_epoch,
            };
            if (
              t.exchange_rate_drift.seed !== undefined &&
              t.exchange_rate_drift.seed !== null
            ) {
              drift.seed = t.exchange_rate_drift.seed;
            }
            out.exchange_rate_drift = drift;
          }
          if (t.transfer_hook) {
            const hook: Record<string, unknown> = {
              additional_cu_per_transfer: t.transfer_hook.additional_cu_per_transfer,
              additional_lamports_per_transfer:
                t.transfer_hook.additional_lamports_per_transfer,
            };
            if (
              t.transfer_hook.program_id !== undefined &&
              t.transfer_hook.program_id !== null
            ) {
              hook.program_id = t.transfer_hook.program_id;
            }
            out.transfer_hook = hook;
          }
          if (t.confidential !== undefined) out.confidential = t.confidential;
          return out;
        })
      : marketType === "clob"
        ? [
            { id: "BASE", symbol: "BASE", decimals: spec.market.token_decimals },
            { id: "QUOTE", symbol: "QUOTE", decimals: spec.market.token_decimals },
          ]
        : [
            { id: "YES", symbol: "YES", decimals: spec.market.token_decimals },
            { id: "NO", symbol: "NO", decimals: spec.market.token_decimals },
          ];
  const collateralTokenId = spec.market.collateral_token_id ?? "COLLATERAL";
  const feeModel = feeModelToApi(spec.fee_model);

  const total = Math.max(spec.agents.total, 1);
  const agents: Record<string, unknown>[] = [];

  const groups = spec.agents.groups;
  if (groups && groups.length > 0) {
    // US-012: dynamic-group emission path. Each group's params are
    // schema-driven and already match the backend agent dataclass
    // shape, so they pass through verbatim. We still zero-fill
    // `collateral` so a group that forgot it doesn't break the
    // backend's balance lookup.
    const useExplicitCounts = groups.some((g) => typeof g.count === "number");
    const counts = useExplicitCounts
      ? groups.map((g) => Math.max(0, g.count ?? 0))
      : (() => {
          const weights = groups.map((g) => Math.max(0, g.weight));
          const totalWeight = weights.reduce((s, w) => s + w, 0);
          const raw = weights.map((w) =>
            totalWeight > 0 ? Math.round((w / totalWeight) * total) : 0,
          );
          const drift = total - raw.reduce((s, n) => s + n, 0);
          if (raw.length > 0) raw[0] += drift;
          return raw;
        })();
    groups.forEach((group, i) => {
      const count = Math.max(0, counts[i]);
      // Don't blindly inject `collateral` — agents like swap_noise
      // and jito_searcher don't accept that kwarg and the backend
      // will reject the run with `unexpected keyword argument
      // 'collateral'`. Trust whatever the user/template configured
      // in `group.params` (registry defaults populate it for the
      // agent types that need it via `entity.defaults`).
      const params: Record<string, unknown> = { ...group.params };
      const prefix = group.agentIdPrefix ?? group.type;
      const balances =
        group.initialBalances && Object.keys(group.initialBalances).length > 0
          ? group.initialBalances
          : { [collateralTokenId]: spec.agents.default_collateral };
      for (let n = 0; n < count; n++) {
        agents.push({
          type: group.type,
          agent_id: count === 1 ? prefix : `${prefix}-${n + 1}`,
          params,
          initial_balances: balances,
        });
      }
    });
  } else {
    const mix = spec.agents.mix;
    const keys: (keyof AgentMix)[] = [
      "noise",
      "informed",
      "arbitrageur",
      "manipulator",
      "passive_lp",
      "rebalancing_lp",
    ];
    const rawCounts = keys.map((k) => Math.round(mix[k] * total));
    const drift = total - rawCounts.reduce((s, n) => s + n, 0);
    if (rawCounts.length > 0) rawCounts[0] += drift;

    keys.forEach((role, i) => {
      const count = rawCounts[i];
      const params = roleParamsToApi(role, spec.agents.role_params, collateralTokenId);
      for (let n = 0; n < count; n++) {
        agents.push({
          type: role,
          agent_id: `${role}-${n + 1}`,
          params,
          initial_balances: {
            [collateralTokenId]: spec.agents.default_collateral,
          },
        });
      }
    });
  }

  if (agents.length === 0) {
    agents.push({
      type: "noise",
      agent_id: "noise-1",
      params: { collateral: collateralTokenId, frequency: 0 },
      initial_balances: {
        [collateralTokenId]: spec.agents.default_collateral,
      },
    });
  }

  let marketBlock: Record<string, unknown>;
  if (marketType === "world" && spec.world && spec.world.markets.length > 0) {
    const markets: Record<string, unknown> = {};
    for (const block of spec.world.markets) {
      markets[block.label || block.id] = worldMarketToApi(
        block,
        spec.market.token_decimals,
        spec.market.initial_liquidity,
      );
    }
    marketBlock = { type: "world", markets };
  } else {
    const marketParams: Record<string, unknown> = {
      initial_liquidity: spec.market.initial_liquidity,
      collateral_token: collateralTokenId,
    };
    if (marketType === "whirlpool" && spec.market.whirlpool_params) {
      for (const [k, v] of Object.entries(spec.market.whirlpool_params)) {
        if (v !== undefined && v !== null && v !== "") marketParams[k] = v;
      }
    }
    marketBlock = {
      type: marketType === "world" ? "cfamm" : marketType,
      tokens,
      fee_model: feeModel,
      params: marketParams,
    };
  }

  const body: Record<string, unknown> = {
    market: marketBlock,
    agents,
    num_rounds: spec.config.num_rounds,
    snapshot_interval: spec.config.snapshot_interval,
    seed: spec.config.seed,
    numeric_mode: spec.config.numeric_mode === "FLOAT_MODE" ? "float" : "fixed",
    clock: clockToApi(spec),
    execution: executionToApi(spec),
    information_filter: informationFilterToApi(spec),
    default_fee_model: feeModel,
    feeds: spec.feeds.map((feed) => feedToApi(feed, spec.config.seed)),
  };
  if (spec.alts && spec.alts.length > 0) {
    body.alts = spec.alts.map((alt) => ({
      id: alt.id,
      entries: alt.entries,
    }));
  }
  return body;
}

// ── Run mapping ────────────────────────────────────────────────────────────

export function fromApiRun(raw: ApiRun): SimRun {
  const summary = raw.summary || {};
  const totalRounds = summary.num_rounds ?? raw.spec?.num_rounds ?? raw.current_round ?? 0;
  const status = mapStatus(raw.status);
  const claim = summary["mainnet_accuracy_claim"];
  const replayKindRaw = summary["replay_kind"];
  const calibration =
    typeof claim === "boolean"
      ? {
          isCalibratedReplay: claim,
          replayKind:
            typeof replayKindRaw === "string" ? replayKindRaw : undefined,
        }
      : undefined;
  return {
    id: raw.run_id,
    name: synthName(raw),
    market: marketLabelFromApi(raw.market_type, raw.spec),
    agents: summary.agent_count ?? raw.spec?.agents?.length ?? 0,
    currentRound: raw.current_round ?? 0,
    totalRounds,
    status,
    seed: raw.seed ?? 0,
    exec: execLabelFromApi(raw.spec),
    ordering: orderingLabelFromApi(raw.spec),
    fee: feeLabelFromApi(raw.spec),
    feed: feedLabelFromApi(raw.spec),
    createdAt: raw.created_at ?? new Date().toISOString(),
    spec: specFromApi(raw.spec),
    calibration,
  };
}

export function fromApiRuns(raws: ApiRun[]): SimRun[] {
  return raws.map(fromApiRun);
}

// ── Result → AgentRow[] / Metrics / chart data ─────────────────────────────

const ROLE_ALIASES: Record<string, string> = {
  arbitrageur: "arb",
  manipulator: "manip",
  passive_lp: "lp",
  rebalancing_lp: "rebal",
};

function normalizeRole(raw: string | undefined): string {
  if (!raw) return "noise";
  const key = raw.toLowerCase();
  return ROLE_ALIASES[key] ?? key;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

// Composite-score sub-scores. Each maps a metric to [0, 1] where 1 = "best
// for the LP / market health". Curves are calibrated for short-horizon
// (~hundreds-of-rounds) Solana templates whose realistic value ranges are:
//
//   maxDrawdown:    -0.001%  to  -0.05%   (percent units)
//   rollingVol:      1e-5    to   1e-3    (per-step return std dev)
//   lpProfitability: 1.0     to   1.01    (1 + yield over the run)
//
// Long-horizon / chain-neutral templates that produce 1-100% drawdowns or
// 1.5x LP yields will saturate these curves at 0; in that regime the
// constants below should be revisited (or the curves reshaped per-template).
const MDD_FULL_PENALTY_PCT = 0.05;          // 0.05% drawdown → score 0
const RVOL_FULL_PENALTY = 1e-3;             // 0.1% per-step vol → score 0
const LP_HALF_SATURATION_YIELD = 0.0005;    // 5 bps run-yield → score 0.5

function scoreMaxDrawdown(maxDrawdown: number): number {
  return clamp01(1 - Math.abs(maxDrawdown) / MDD_FULL_PENALTY_PCT);
}

function scoreRollingVol(rollingVol: number): number {
  return clamp01(1 - rollingVol / RVOL_FULL_PENALTY);
}

function scoreLpProfitability(lpProfitability: number): number {
  if (!Number.isFinite(lpProfitability) || lpProfitability <= 1) return 0;
  // Sigmoid in fee yield. Reaches 0.5 at ``LP_HALF_SATURATION_YIELD``,
  // 0.91 at 5x that, asymptotes to 1.0. Smoother than a linear-clamp
  // and immune to the run-length-dependent saturation a linear curve
  // would have.
  const yieldOverRun = lpProfitability - 1;
  return yieldOverRun / (yieldOverRun + LP_HALF_SATURATION_YIELD);
}

function computeCompositeScore(metrics: Pick<SimMetrics, "maxDrawdown" | "lpProfitability" | "rollingVol">): number {
  // ``null`` lpProfitability — e.g. no fee_history available — is
  // treated as the neutral 1 (no yield earned, no loss recorded) so
  // composite stays defined for legacy result shapes.
  const lp = metrics.lpProfitability ?? 1;
  const total =
    scoreMaxDrawdown(metrics.maxDrawdown) +
    scoreLpProfitability(lp) +
    scoreRollingVol(metrics.rollingVol);
  return Math.round((total / 3) * 100);
}

export function agentRowsFromResult(result: ApiRunResult): AgentRow[] {
  const states = result.agent_final_states || {};
  const rows: AgentRow[] = [];
  let idx = 0;
  for (const [key, state] of Object.entries(states)) {
    const balances = state.balances || {};
    const balance = Object.values(balances).reduce(
      (s: number, v) => s + (typeof v === "number" ? v : 0),
      0,
    );
    const cleanBalances: Record<string, number> = {};
    for (const [k, v] of Object.entries(balances)) {
      if (typeof v === "number") cleanBalances[k] = v;
    }
    const agentId = state.agent_id != null ? String(state.agent_id) : String(key);
    rows.push({
      id: idx++,
      agentId,
      role: normalizeRole(state.role?.name),
      balance,
      volume: state.cumulative_volume ?? 0,
      volumeQuote:
        typeof state.cumulative_volume_quote === "number"
          ? state.cumulative_volume_quote
          : undefined,
      pnl: state.realized_pnl ?? 0,
      trades: 0,
      balances: cleanBalances,
    });
  }
  return rows;
}

/**
 * Pluck every finite numeric entry from ``metadata.derived_metrics`` so the
 * results dashboard can render one tile per metric without hard-coding the
 * key set. ``Infinity`` is preserved (the ``fees_vs_il_breakeven`` metric
 * uses it as a sentinel for "fees collected with zero realized IL"); only
 * ``null`` and ``NaN`` are filtered out.
 */
export function derivedNumericMetrics(result: ApiRunResult): Record<string, number> {
  const derived = result.metadata?.derived_metrics ?? {};
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(derived)) {
    if (typeof value !== "number") continue;
    if (Number.isNaN(value)) continue;
    out[key] = value;
  }
  return out;
}

export function metricsFromResult(result: ApiRunResult): SimMetrics {
  const prices = pricesFromHistory(result.price_history);
  const liquidity = result.liquidity_history || [];

  const derived = result.metadata?.derived_metrics ?? {};
  const klDivergence = readDerivedNumber(derived.kl_divergence);
  const convergenceSpeed = readDerivedNumber(derived.convergence_speed);
  const sandwich = sandwichTotalsFromResult(result);
  const numRoundsForStress =
    result.num_rounds_executed ?? result.num_rounds ?? prices.length ?? 0;
  const stressScore = scoreStress(
    sandwich.realizedEvLamports,
    numRoundsForStress,
  );
  // Prefer the fee_history-derived LP fee yield. Falls through to the
  // liquidity_history ratio (legacy unit tests, pre-fee_history fixtures)
  // when fee_history / agent_final_states / a USDC-like quote token aren't
  // all present. Returns ``null`` when neither source has data so the
  // dashboard renders ``—`` instead of the misleading 1.000 placeholder
  // the old code emitted as a divide-by-zero default.
  const feeYieldRatio = lpFeeYieldRatio(result);
  const lpProfitability: number | null =
    feeYieldRatio !== null
      ? feeYieldRatio
      : liquidity.length > 1 && liquidity[0] > 0
        ? liquidity[liquidity.length - 1] / liquidity[0]
        : null;
  const manipulationCost = readDerivedNumber(derived.manipulation_cost);
  const maxDrawdown = prices.length > 0 ? computeMaxDrawdown(prices) : 0;
  const rollingVol = prices.length > 0 ? computeRollingVol(prices) : 0;
  const twap = prices.length > 0 ? prices.reduce((s, p) => s + p, 0) / prices.length : 0;
  const slippage = readDerivedNumber(derived.slippage);
  const exitability = readDerivedNumber(derived.exitability);
  const compositeScore =
    prices.length > 0 || liquidity.length > 1 || feeYieldRatio !== null
      ? computeCompositeScore({
          maxDrawdown,
          // The composite formula assumes ``>= 1`` is "no fee earned":
          // fall back to that floor when the fee yield isn't computable
          // so the score stays defined for legacy result shapes.
          lpProfitability: lpProfitability ?? 1,
          rollingVol,
        })
      : 0;

  const tickCrossings = totalTickCrossingsFromRounds(result.round_snapshots);

  return {
    klDivergence,
    convergenceSpeed,
    lpProfitability,
    manipulationCost,
    maxDrawdown,
    rollingVol,
    twap,
    slippage,
    exitability,
    compositeScore,
    stressScore,
    sandwichBundlesLanded: sandwich.bundlesLanded,
    sandwichBundlesSubmitted: sandwich.bundlesSubmitted,
    sandwichRealizedEvLamports: sandwich.realizedEvLamports,
    tickCrossings,
  };
}

function totalTickCrossingsFromRounds(
  roundSnapshots: ApiRunResult["round_snapshots"],
): number {
  if (!Array.isArray(roundSnapshots) || roundSnapshots.length === 0) return 0;
  let total = 0;
  for (const snapshot of roundSnapshots) {
    if (!isPlainObject(snapshot)) continue;
    const metrics = snapshot["metrics"];
    const whirlpool = isPlainObject(metrics) ? metrics["whirlpool"] : undefined;
    if (!isPlainObject(whirlpool)) continue;
    for (const entry of Object.values(whirlpool)) {
      if (!isPlainObject(entry)) continue;
      total += toChartNumber(entry["tick_crossings"]);
    }
  }
  return total;
}

interface SandwichTotals {
  bundlesLanded: number;
  bundlesSubmitted: number;
  realizedEvLamports: number;
}

/**
 * Sum every searcher × strategy bucket on the *last* round snapshot's
 * ``metrics.jito_searcher`` payload — the per-strategy counters are
 * cumulative for the run so the final snapshot already holds the totals.
 *
 * Falls back to zeros when no searcher ran (or no Solana-style snapshots
 * were captured) so non-Solana templates and pre-jito-searcher results
 * still produce a valid ``SimMetrics`` shape.
 */
function sandwichTotalsFromResult(result: ApiRunResult): SandwichTotals {
  const out: SandwichTotals = {
    bundlesLanded: 0,
    bundlesSubmitted: 0,
    realizedEvLamports: 0,
  };
  const snapshots = result.round_snapshots;
  if (!Array.isArray(snapshots) || snapshots.length === 0) return out;
  const last = snapshots[snapshots.length - 1];
  if (!isPlainObject(last)) return out;
  const metrics = last["metrics"];
  if (!isPlainObject(metrics)) return out;
  const jito = metrics["jito_searcher"];
  if (!isPlainObject(jito)) return out;

  for (const searcher of Object.values(jito)) {
    if (!isPlainObject(searcher)) continue;
    const byStrategy = searcher["by_strategy"];
    if (!isPlainObject(byStrategy)) continue;
    for (const bucket of Object.values(byStrategy)) {
      if (!isPlainObject(bucket)) continue;
      out.bundlesLanded += toChartNumber(bucket["bundles_landed"]);
      out.bundlesSubmitted += toChartNumber(bucket["bundles_submitted"]);
      out.realizedEvLamports += toChartNumber(bucket["realized_ev_lamports"]);
    }
  }
  return out;
}

// Sigmoid in "EV per slot" so the score lives on a 0–100 scale that rises
// with searcher success but never saturates at 100. The half-saturation
// constant (1M lamports/slot ≈ 1bp of a 1-SOL pool depth per slot) is
// calibrated for Solana-style sandwich templates — long-horizon or
// non-Solana templates may need a different anchor. Empty / no-searcher
// runs collapse to 0.
const STRESS_HALF_SATURATION_EV_PER_SLOT = 1_000_000;

function scoreStress(realizedEvLamports: number, numRounds: number): number {
  if (!Number.isFinite(realizedEvLamports) || realizedEvLamports <= 0) return 0;
  if (!Number.isFinite(numRounds) || numRounds <= 0) return 0;
  const evPerSlot = realizedEvLamports / numRounds;
  const ratio = evPerSlot / (evPerSlot + STRESS_HALF_SATURATION_EV_PER_SLOT);
  return Math.round(ratio * 100);
}

function readDerivedNumber(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pricesFromHistory(history: ApiRunResult["price_history"]): number[] {
  if (!Array.isArray(history) || history.length === 0) return [];
  const first = history[0];
  if (typeof first !== "object" || first === null) return [];
  const keys = Object.keys(first);
  if (keys.length === 0) return [];
  const primary = keys[0];
  return history.map((h) => (typeof h[primary] === "number" ? h[primary] : 0));
}

/**
 * "LP fee yield over the run" expressed as an accrual ratio (1 + yield), so
 * 1.005 reads as "LPs earned 0.5% on capital over the run". The composite-
 * score formula treats lpProfitability as a 0–2 ratio (1 = neutral), so
 * preserving that semantic keeps the existing score weights working.
 *
 * Numerator = ``lp``-destination fees from ``fee_history``, summed across
 * rounds in the AMM's *quote* token only (the one whose spot price is ≈ 1
 * in the last price snapshot — for SOL/USDC that's USDC). Restricting to
 * the quote token avoids cross-decimal arithmetic without surfacing token
 * decimals into this layer; the trade-off is missing the fees collected on
 * the other side of the book (≈ half the total in a balanced market). v2
 * can pull token decimals from the spec to convert all fees into a single
 * unit.
 *
 * Denominator = the ``passive_lp`` agent's final balance in that same
 * token, summed across all such agents. ``passive_lp`` doesn't trade, so
 * its balance stays at the initial deposit — no need to plumb the spec
 * just to recover capital_deposited.
 *
 * Returns ``null`` when any required data is missing (no fee_history, no
 * detectable quote token, no passive_lp agent, or zero LP balance) so the
 * caller can fall back to legacy paths instead of misreporting a NaN.
 */
function lpFeeYieldRatio(result: ApiRunResult): number | null {
  const collateralToken = detectQuoteToken(result.price_history);
  if (!collateralToken) return null;

  const lpFees = sumLpFeesForToken(result.fee_history, collateralToken);
  const lpBalance = passiveLpBalanceForToken(
    result.agent_final_states,
    collateralToken,
  );
  if (lpBalance <= 0) return null;
  return 1 + lpFees / lpBalance;
}

function detectQuoteToken(
  history: ApiRunResult["price_history"],
): string | null {
  if (!Array.isArray(history) || history.length === 0) return null;
  const last = history[history.length - 1];
  if (!isPlainObject(last)) return null;
  for (const [token, price] of Object.entries(last)) {
    if (typeof price === "number" && Math.abs(price - 1) < 0.01) {
      return token;
    }
  }
  return null;
}

function sumLpFeesForToken(
  feeHistory: ApiRunResult["fee_history"],
  token: string,
): number {
  if (!Array.isArray(feeHistory)) return 0;
  let total = 0;
  for (const splits of feeHistory) {
    if (!isPlainObject(splits)) continue;
    const lp = splits["lp"];
    if (!isPlainObject(lp)) continue;
    const value = lp[token];
    if (value !== undefined) {
      total += toChartNumber(value);
    }
  }
  return total;
}

function passiveLpBalanceForToken(
  states: ApiRunResult["agent_final_states"],
  token: string,
): number {
  if (!states || typeof states !== "object") return 0;
  let total = 0;
  for (const state of Object.values(states)) {
    const role = state?.role?.name;
    if (role !== "passive_lp" && role !== "lp") continue;
    const balance = state.balances?.[token];
    if (typeof balance === "number") total += balance;
  }
  return total;
}

function computeMaxDrawdown(prices: number[]): number {
  let peak = prices[0];
  let maxDd = 0;
  for (const p of prices) {
    if (p > peak) peak = p;
    const dd = peak > 0 ? (p - peak) / peak : 0;
    if (dd < maxDd) maxDd = dd;
  }
  return maxDd * 100;
}

function computeRollingVol(prices: number[], window = 20): number {
  if (prices.length < 2) return 0;
  const returns: number[] = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] === 0) continue;
    returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
  }
  const slice = returns.slice(-window);
  if (slice.length === 0) return 0;
  const mean = slice.reduce((s, x) => s + x, 0) / slice.length;
  const variance = slice.reduce((s, x) => s + (x - mean) ** 2, 0) / slice.length;
  return Math.sqrt(variance);
}

export interface FeeDestinationSeries {
  destination: string;
  data: number[];
}

export interface ChartData {
  priceData: number[][];
  priceLabels: string[];
  cumVol: number[];
  liq: number[];
  fees: number[];
  // Cumulative fee series, one entry per `fee_history` split key
  // (e.g. `lp`, `protocol`, `burn`). Each series' `data[r]` is the
  // running total of *only this destination* at round r, sorted by
  // per-destination total descending. Render with the chart layer's
  // stacked mode so tooltips surface true per-destination values; the
  // chart stacks the visual bands so fixed-bps splits (e.g. 50/50 lp +
  // protocol) don't collapse into a single overlapping line.
  feesByDestination: FeeDestinationSeries[];
  pnlData: number[];
  pnlColors: string[];
  // Whirlpool/CLMM-only series (empty arrays for non-Whirlpool runs).
  // ``tickCrossings`` is per-round (not cumulative). ``activeLiquidity``
  // tracks the L the price is sitting on (steps every time a swap
  // crosses an initialized tick). ``totalLpLiquidity`` is the sum of L
  // across every minted position regardless of in-range status — it
  // only steps on mint/burn events. ``baselineLpLiquidity`` is the
  // construction-time snapshot of total deposited L (chain-hydrated
  // for fork runs, zero otherwise) and is constant across all
  // rounds; ``agentLpLiquidity`` is the per-round delta (total −
  // baseline). The Total LP Deposits chart stacks them: baseline as a
  // floor, agent activity on top. ``feesA`` / ``feesB`` are
  // cumulative LP fees in token-A and token-B units respectively.
  tickCrossings: number[];
  activeLiquidity: number[];
  totalLpLiquidity: number[];
  baselineLpLiquidity: number[];
  agentLpLiquidity: number[];
  feesA: number[];
  feesB: number[];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// The backend serializes integers outside JS's safe range as
// `{"__defi_sim_bigint__": "<digits>"}` (see engine/json.py). Chart code
// only needs a plottable Number, so collapse both encodings here; the
// precision loss on huge fixed-point fees is acceptable for visuals.
const BIGINT_MARKER = "__defi_sim_bigint__";
function toChartNumber(value: unknown): number {
  if (typeof value === "number") return value;
  if (isPlainObject(value)) {
    const encoded = value[BIGINT_MARKER];
    if (typeof encoded === "string") {
      const n = Number(encoded);
      return Number.isFinite(n) ? n : 0;
    }
  }
  return 0;
}

function normalizeMarketKey(key: string): string {
  return key.trim().toLowerCase();
}

function splitWorldPriceKey(key: string): { market: string | null; label: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { market: null, label: key };
  return {
    market: normalizeMarketKey(key.slice(0, idx)),
    label: key.slice(idx + 1),
  };
}

function liquiditySeriesFromRounds(
  roundSnapshots: ApiRunResult["round_snapshots"],
  market?: string,
): number[] {
  if (!Array.isArray(roundSnapshots) || roundSnapshots.length === 0) return [];
  const normalized = market ? normalizeMarketKey(market) : null;
  const values: number[] = [];
  for (const snapshot of roundSnapshots) {
    if (!isPlainObject(snapshot)) continue;
    let marketState: Record<string, unknown> | null = null;
    if (market) {
      const allStates = snapshot["all_market_states"];
      if (isPlainObject(allStates)) {
        const exact = allStates[market];
        const fallback = normalized ? allStates[normalized] : undefined;
        marketState = isPlainObject(exact)
          ? exact
          : isPlainObject(fallback)
            ? fallback
            : null;
      }
    } else {
      const single = snapshot["market_state"];
      if (isPlainObject(single)) {
        marketState = single;
      } else {
        const allStates = snapshot["all_market_states"];
        if (isPlainObject(allStates)) {
          let total = 0;
          let found = false;
          for (const v of Object.values(allStates)) {
            if (!isPlainObject(v)) continue;
            const raw = v["total_liquidity"];
            if (typeof raw === "number" || isPlainObject(raw)) {
              total += toChartNumber(raw);
              found = true;
            }
          }
          if (found) {
            values.push(total);
            continue;
          }
        }
      }
    }
    if (!marketState) continue;
    const totalLiquidity = marketState["total_liquidity"];
    if (typeof totalLiquidity === "number" || isPlainObject(totalLiquidity)) {
      values.push(toChartNumber(totalLiquidity));
    }
  }
  return values;
}

// The backend emits fees keyed by destination and then by token
// (`{lp: {USDC: 5, ETH: 2}, protocol: {…}}`) so mixed-token runs don't
// collapse different tokens into one scalar. The flat-number shape is
// still accepted for older results / unit-test fixtures that pre-date
// the token-aware migration. Both shapes are summed here because the
// Cumulative Fees chart only needs a per-round scalar.
//
// Summing across tokens is only meaningful when tokens share a
// numeraire; if that assumption fails, the Fees by Destination chart
// should be preferred because it keeps destination (and future: token)
// identity visible.
function sumFeeSplitValue(value: unknown): number {
  if (isPlainObject(value)) {
    if (typeof value[BIGINT_MARKER] === "string") return toChartNumber(value);
    let total = 0;
    for (const inner of Object.values(value)) total += toChartNumber(inner);
    return total;
  }
  return toChartNumber(value);
}

function totalFeesPerRound(
  feeHistory: ApiRunResult["fee_history"],
): number[] {
  if (!Array.isArray(feeHistory)) return [];
  return feeHistory.map((splits) => {
    if (!isPlainObject(splits)) return 0;
    let total = 0;
    for (const v of Object.values(splits)) total += sumFeeSplitValue(v);
    return total;
  });
}

// Pivot `fee_history` into one cumulative series per destination.
// Each `data[r]` is the running total of just this destination — NOT
// pre-stacked — so tooltips can surface true per-destination values.
// Visual stacking is the chart layer's job (pass `stacked` to
// ChartCanvas). Sorted by per-destination total descending so the
// largest band sits at the bottom of the stack.
function cumulativeFeesByDestination(
  feeHistory: ApiRunResult["fee_history"],
): FeeDestinationSeries[] {
  if (!Array.isArray(feeHistory) || feeHistory.length === 0) return [];
  const destinations = new Set<string>();
  for (const splits of feeHistory) {
    if (!isPlainObject(splits)) continue;
    for (const key of Object.keys(splits)) destinations.add(key);
  }
  if (destinations.size === 0) return [];

  const perDestination: FeeDestinationSeries[] = [];
  for (const destination of destinations) {
    const data: number[] = [];
    let running = 0;
    for (const splits of feeHistory) {
      const value = isPlainObject(splits) ? splits[destination] : undefined;
      running += sumFeeSplitValue(value);
      data.push(running);
    }
    perDestination.push({ destination, data });
  }
  perDestination.sort((a, b) => {
    const lastA = a.data[a.data.length - 1] ?? 0;
    const lastB = b.data[b.data.length - 1] ?? 0;
    return lastB - lastA;
  });
  return perDestination;
}

// Sum cumulative_volume across all agents at each round snapshot. The
// backend doesn't emit a top-level volume_history, but per-agent
// cumulative volume is recorded on every snapshot, so summing across
// agents gives a market-wide cumulative series.
function cumulativeVolumeFromRounds(
  roundSnapshots: ApiRunResult["round_snapshots"],
): number[] {
  if (!Array.isArray(roundSnapshots) || roundSnapshots.length === 0) return [];
  const values: number[] = [];
  for (const snapshot of roundSnapshots) {
    if (!isPlainObject(snapshot)) continue;
    const agentStates = snapshot["agent_states"];
    if (!isPlainObject(agentStates)) continue;
    let total = 0;
    for (const state of Object.values(agentStates)) {
      if (!isPlainObject(state)) continue;
      total += toChartNumber(state["cumulative_volume"]);
    }
    values.push(total);
  }
  return values;
}

// Walk RoundSnapshot.metrics.whirlpool[<market>] and emit per-round series
// for tick crossings, active liquidity, total LP liquidity, and per-side
// LP fees. When the run has multiple Whirlpool markets, sum tick_crossings
// across them, take the last-seen active_liquidity (single-market is the
// canonical demo case), sum total_lp_liquidity, and sum per-side fees.
// Returns zero-length arrays for non-Whirlpool runs.
function whirlpoolSeriesFromRounds(
  roundSnapshots: ApiRunResult["round_snapshots"],
  market?: string,
): {
  tickCrossings: number[];
  activeLiquidity: number[];
  totalLpLiquidity: number[];
  baselineLpLiquidity: number[];
  agentLpLiquidity: number[];
  feesA: number[];
  feesB: number[];
} {
  const empty = {
    tickCrossings: [] as number[],
    activeLiquidity: [] as number[],
    totalLpLiquidity: [] as number[],
    baselineLpLiquidity: [] as number[],
    agentLpLiquidity: [] as number[],
    feesA: [] as number[],
    feesB: [] as number[],
  };
  if (!Array.isArray(roundSnapshots) || roundSnapshots.length === 0) return empty;

  const tickCrossings: number[] = [];
  const activeLiquidity: number[] = [];
  const totalLpLiquidity: number[] = [];
  const baselineLpLiquidity: number[] = [];
  const agentLpLiquidity: number[] = [];
  let runningFeesA = 0;
  let runningFeesB = 0;
  const feesA: number[] = [];
  const feesB: number[] = [];
  let sawAny = false;

  for (const snapshot of roundSnapshots) {
    if (!isPlainObject(snapshot)) {
      tickCrossings.push(0);
      activeLiquidity.push(0);
      totalLpLiquidity.push(0);
      baselineLpLiquidity.push(0);
      agentLpLiquidity.push(0);
      feesA.push(runningFeesA);
      feesB.push(runningFeesB);
      continue;
    }
    const metrics = snapshot["metrics"];
    const whirlpool = isPlainObject(metrics) ? metrics["whirlpool"] : undefined;
    if (!isPlainObject(whirlpool)) {
      tickCrossings.push(0);
      activeLiquidity.push(0);
      totalLpLiquidity.push(0);
      baselineLpLiquidity.push(0);
      agentLpLiquidity.push(0);
      feesA.push(runningFeesA);
      feesB.push(runningFeesB);
      continue;
    }
    sawAny = true;

    let crossings = 0;
    let activeL = 0;
    let totalLpL = 0;
    let baselineLpL = 0;
    let agentLpL = 0;
    let stepFeesA = 0;
    let stepFeesB = 0;
    const entries = market ? [whirlpool[market]] : Object.values(whirlpool);
    for (const entry of entries) {
      if (!isPlainObject(entry)) continue;
      crossings += toChartNumber(entry["tick_crossings"]);
      activeL = toChartNumber(entry["active_liquidity"]);
      totalLpL += toChartNumber(entry["total_lp_liquidity"]);
      baselineLpL += toChartNumber(entry["baseline_lp_liquidity"]);
      agentLpL += toChartNumber(entry["agent_lp_liquidity"]);
      stepFeesA += toChartNumber(entry["lp_fees_a"]);
      stepFeesB += toChartNumber(entry["lp_fees_b"]);
    }
    runningFeesA += stepFeesA;
    runningFeesB += stepFeesB;
    tickCrossings.push(crossings);
    activeLiquidity.push(activeL);
    totalLpLiquidity.push(totalLpL);
    baselineLpLiquidity.push(baselineLpL);
    agentLpLiquidity.push(agentLpL);
    feesA.push(runningFeesA);
    feesB.push(runningFeesB);
  }

  if (!sawAny) return empty;
  return {
    tickCrossings,
    activeLiquidity,
    totalLpLiquidity,
    baselineLpLiquidity,
    agentLpLiquidity,
    feesA,
    feesB,
  };
}

export function chartDataFromResult(
  result: ApiRunResult,
  options: { market?: string | null } = {},
): ChartData {
  const { labels: priceLabels, series: priceData } = priceSeriesFromHistory(
    result.price_history,
    options.market ?? undefined,
  );
  const vols = result.volume_history || [];
  const cumVolFromHistory = vols.length > 0 ? cumulative(vols) : [];
  const cumVol =
    cumVolFromHistory.length > 0
      ? cumVolFromHistory
      : cumulativeVolumeFromRounds(result.round_snapshots);
  const marketLiquidity = liquiditySeriesFromRounds(
    result.round_snapshots,
    options.market ?? undefined,
  );
  const liq = marketLiquidity.length > 0 ? marketLiquidity : result.liquidity_history || [];
  const fees = cumulative(totalFeesPerRound(result.fee_history));
  const feesByDestination = cumulativeFeesByDestination(result.fee_history);
  const pnlData: number[] = [];
  const pnlColors: string[] = [];
  const states = result.agent_final_states || {};
  for (const state of Object.values(states)) {
    const pnl = state.realized_pnl ?? 0;
    pnlData.push(pnl);
    pnlColors.push(pnl >= 0 ? "#34d399" : "#f87171");
  }
  const {
    tickCrossings,
    activeLiquidity,
    totalLpLiquidity,
    baselineLpLiquidity,
    agentLpLiquidity,
    feesA,
    feesB,
  } = whirlpoolSeriesFromRounds(
    result.round_snapshots,
    options.market ?? undefined,
  );
  // For Whirlpool runs prefer ``total_lp_liquidity`` over the
  // AmmSnapshot's ``total_liquidity`` (which is just active in-range
  // L). The "Total LP Deposits Over Time" card reads ``liq`` for its
  // single-series fallback (non-Whirlpool runs); for Whirlpool runs
  // it stacks ``baselineLpLiquidity`` + ``agentLpLiquidity`` directly.
  const liqForChart = totalLpLiquidity.length > 0 ? totalLpLiquidity : liq;
  return {
    priceData,
    priceLabels,
    cumVol,
    liq: liqForChart,
    fees,
    feesByDestination,
    pnlData,
    pnlColors,
    tickCrossings,
    activeLiquidity,
    totalLpLiquidity,
    baselineLpLiquidity,
    agentLpLiquidity,
    feesA,
    feesB,
  };
}

function priceSeriesFromHistory(
  history: ApiRunResult["price_history"],
  market?: string,
): { labels: string[]; series: number[][] } {
  if (!Array.isArray(history) || history.length === 0) return { labels: [], series: [] };
  const first = history[0];
  if (typeof first !== "object" || first === null) return { labels: [], series: [] };
  const keys = Object.keys(first);
  const normalizedMarket = market ? normalizeMarketKey(market) : null;
  const filteredKeys =
    normalizedMarket == null
      ? keys
      : keys.filter((key) => splitWorldPriceKey(key).market === normalizedMarket);
  const finalKeys = filteredKeys.length > 0 ? filteredKeys : keys;
  return {
    labels: finalKeys.map((key) => {
      const parsed = splitWorldPriceKey(key);
      return normalizedMarket == null ? key : parsed.label;
    }),
    series: finalKeys.map((key) =>
      history.map((h) => (typeof h[key] === "number" ? h[key] : 0)),
    ),
  };
}

function cumulative(values: number[]): number[] {
  const out: number[] = [];
  let sum = 0;
  for (const v of values) {
    sum += typeof v === "number" ? v : 0;
    out.push(sum);
  }
  return out;
}

// ── Events ─────────────────────────────────────────────────────────────────

// US-015: event classification is derived from a deterministic hash
// rather than a hardcoded coverage map. Unknown event kinds get a
// stable CSS class from the existing .event-log .ev-type.* palette
// so new backend events render without frontend coverage edits. See
// `src/lib/utils/hashColor.ts` for the hash + palette definitions.
function classifyEvent(type: string | undefined): string {
  if (!type) return hashEventClass("UNKNOWN");
  return hashEventClass(type);
}

function describeEvent(raw: ApiEventRaw): string {
  const data = raw.data || {};
  const type = raw.type || "";
  if (type === "SIMULATION_START") {
    const runId = typeof data.run_id === "string" ? data.run_id : "";
    return `Engine initialized${runId ? ` · ${runId}` : ""}`;
  }
  if (type === "SIMULATION_END") {
    return "Simulation complete";
  }
  if (type === "ACTION_EXECUTED") {
    const agent = typeof data.agent_id === "string" ? data.agent_id : "agent";
    const action = typeof data.action === "string" ? data.action : "acted";
    return `${agent} ${action}`;
  }
  if (type === "ACTION_FAILED") {
    const agent = typeof data.agent_id === "string" ? data.agent_id : "agent";
    const reason = typeof data.reason === "string" ? data.reason : "failed";
    return `${agent} failed: ${reason}`;
  }
  if (type === "EPOCH_BOUNDARY") {
    return `epoch ${data.epoch ?? "?"}`;
  }
  if (type === "ROUND_START" || type === "ROUND_END") {
    return `round ${raw.round ?? "?"}`;
  }
  return type.toLowerCase();
}

export function fromApiEvent(raw: ApiEventRaw): EvEntry {
  return {
    round: raw.round ?? 0,
    evType: raw.type || "UNKNOWN",
    cls: classifyEvent(raw.type),
    detail: describeEvent(raw),
    data: raw.data,
  };
}

export function fromApiEvents(raws: ApiEventRaw[]): EvEntry[] {
  return raws.map(fromApiEvent);
}

// ── Priority fee market chart (PRD US-010 line 748) ───────────────────────
// Derives a per-account percentile time series from the
// `PRIORITY_FEE_MARKET_UPDATED` event stream. The engine emits one event
// per slot per account whose distribution shifted past the configured
// threshold; each event carries the post-update percentile dict in
// micro-lamports per CU. The results page renders one line per
// (account, percentile) so users can see how write-lock contention
// re-prices a hot pool over the run.

const PRIORITY_FEE_MARKET_EVENT_TYPE = "PRIORITY_FEE_MARKET_UPDATED";
const FEE_MARKET_CHART_PERCENTILES = [25, 50, 75, 90, 99] as const;
const FEE_MARKET_CHART_MAX_ACCOUNTS = 4;

export interface PriorityFeeMarketSeries {
  accountId: string;
  percentile: number;
  data: number[];
}

export interface PriorityFeeMarketChart {
  rounds: number[];
  series: PriorityFeeMarketSeries[];
  accounts: string[];
}

interface MappingMarker {
  __type__: "mapping";
  entries: Array<{ key: unknown; value: unknown }>;
}

function isMappingMarker(value: unknown): value is MappingMarker {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    v.__type__ === "mapping" &&
    Array.isArray(v.entries)
  );
}

// `to_jsonable` (engine/json.py) serializes int-keyed dicts via a
// `{__type__: "mapping", entries: [{key, value}]}` envelope, since JSON
// keys must be strings. The percentile dict on
// `PriorityFeeMarketUpdatedEvent` is `dict[int, int]`, so the wire form
// is the marker envelope. This helper normalizes either form back to
// `{ percentile -> price }`.
function decodePercentileMap(raw: unknown): Map<number, number> {
  const out = new Map<number, number>();
  if (!raw) return out;
  if (isMappingMarker(raw)) {
    for (const { key, value } of raw.entries) {
      const k = typeof key === "string" ? Number(key) : (key as number);
      const v = typeof value === "string" ? Number(value) : (value as number);
      if (Number.isFinite(k) && Number.isFinite(v)) out.set(k, v);
    }
    return out;
  }
  if (typeof raw === "object") {
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      const k = Number(key);
      const v = typeof value === "number" ? value : Number(value);
      if (Number.isFinite(k) && Number.isFinite(v)) out.set(k, v);
    }
  }
  return out;
}

interface PriorityFeeMarketUpdate {
  round: number;
  accountId: string;
  percentiles: Map<number, number>;
}

function extractPriorityFeeMarketUpdates(
  events: readonly EvEntry[],
): PriorityFeeMarketUpdate[] {
  const out: PriorityFeeMarketUpdate[] = [];
  for (const ev of events) {
    if (ev.evType !== PRIORITY_FEE_MARKET_EVENT_TYPE) continue;
    const data = ev.data;
    if (!data || typeof data !== "object") continue;
    const payload = (data as Record<string, unknown>)[
      "priority_fee_market_updated"
    ];
    if (!payload || typeof payload !== "object") continue;
    const p = payload as Record<string, unknown>;
    const accountId =
      typeof p.account_id === "string"
        ? p.account_id
        : typeof (data as Record<string, unknown>).account_id === "string"
          ? ((data as Record<string, unknown>).account_id as string)
          : null;
    if (!accountId) continue;
    const round = typeof p.slot === "number" ? p.slot : ev.round;
    const percentiles = decodePercentileMap(p.percentiles);
    if (percentiles.size === 0) continue;
    out.push({ round, accountId, percentiles });
  }
  return out;
}

export function priorityFeeMarketChartFromEvents(
  events: readonly EvEntry[],
  options: { maxAccounts?: number } = {},
): PriorityFeeMarketChart {
  const maxAccounts = options.maxAccounts ?? FEE_MARKET_CHART_MAX_ACCOUNTS;
  const updates = extractPriorityFeeMarketUpdates(events);
  if (updates.length === 0) {
    return { rounds: [], series: [], accounts: [] };
  }

  // Pick the hottest accounts by update-event count (most-active write-lock
  // targets are the interesting ones; cold pools just sit at floor).
  const accountCounts = new Map<string, number>();
  for (const u of updates) {
    accountCounts.set(u.accountId, (accountCounts.get(u.accountId) ?? 0) + 1);
  }
  const accounts = [...accountCounts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, maxAccounts)
    .map(([id]) => id);

  const accountSet = new Set(accounts);
  const filtered = updates.filter((u) => accountSet.has(u.accountId));

  // Shared x-axis: sorted union of all update rounds. Each (account,
  // percentile) series carries the last observed value for that pair at
  // each round (carry-forward fill); rounds before an account's first
  // update get NaN so the chart can leave the line undrawn there.
  const roundsSet = new Set<number>();
  for (const u of filtered) roundsSet.add(u.round);
  const rounds = [...roundsSet].sort((a, b) => a - b);
  const roundIndex = new Map<number, number>();
  rounds.forEach((r, i) => roundIndex.set(r, i));

  const series: PriorityFeeMarketSeries[] = [];
  for (const accountId of accounts) {
    const accountUpdates = filtered.filter((u) => u.accountId === accountId);
    for (const percentile of FEE_MARKET_CHART_PERCENTILES) {
      const data = new Array<number>(rounds.length).fill(NaN);
      let last = NaN;
      let cursor = 0;
      for (let i = 0; i < rounds.length; i++) {
        while (
          cursor < accountUpdates.length &&
          accountUpdates[cursor].round <= rounds[i]
        ) {
          const v = accountUpdates[cursor].percentiles.get(percentile);
          if (typeof v === "number" && Number.isFinite(v)) last = v;
          cursor++;
        }
        data[i] = last;
      }
      series.push({ accountId, percentile, data });
    }
  }

  return { rounds, series, accounts };
}
