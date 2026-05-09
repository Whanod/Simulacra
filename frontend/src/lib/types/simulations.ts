export type SimStatus = "running" | "completed" | "paused" | "cancelled" | "failed";

// Open type aliases (US-003). These are backend-owned identifiers; the
// frontend must not enforce a closed set on them or unknown backend
// values get silently coerced in adapters. ClockType, OrderingModel and
// CostModel stay closed for now — they're opened in US-013.
export type MarketType = string;
export type ClockType = "block" | "variable" | "solana_slot";
export type ExecutionModel = string;
export type OrderingModel = "fifo" | "random" | "priority" | "sandwich" | "block_builder";
export type CostModel = "zero" | "fixed" | "typed" | "eip1559" | "compute_unit";
export type FeeModel = string;
export type InfoFilter = string;
export type FeedType = string;
export type NumericMode = "FIXED_POINT" | "FLOAT_MODE";

export interface AgentMix {
  noise: number;
  informed: number;
  arbitrageur: number;
  manipulator: number;
  passive_lp: number;
  rebalancing_lp: number;
  [key: string]: number;
}

export type RoleKey = string;

export interface RoleParams {
  tradeMin?: number;
  tradeMax?: number;
  frequency?: number;
  conviction?: number;
  priceTolerance?: number;
  attackCapital?: number;
  depositFraction?: number;
  rebalanceInterval?: number;
}

/**
 * US-012: dynamic agent group. Replaces the fixed six-role mix. Each
 * group picks a backend agent type (from the registry contract) and
 * carries a weight (0..100 share of total population) plus schema-
 * driven params that pass straight through to the backend agent
 * dataclass. Unknown agent types ride through the same struct.
 */
export interface AgentGroup {
  id: string;
  type: string;
  weight: number;
  params: Record<string, unknown>;
  /** Explicit agent count for this group. When set, overrides the
   *  weight-based proportional split — lighthouse-style templates
   *  need exact populations like 4 noise / 1 victim-1 / 1 victim-small
   *  / 1 sandwich-1 / 1 lp-1 / 1 searcher-1. */
  count?: number;
  /** Stable backend agent_id stem ("searcher", "victim-small"). When
   *  absent, the adapter generates `${type}-${n}`. */
  agentIdPrefix?: string;
  /** Per-agent initial balances. Backend agents (e.g. swap_noise) may
   *  need both USDC and SOL; the global default_collateral can't
   *  express that. */
  initialBalances?: Record<string, number>;
}

export interface WorldMarketBlock {
  id: string;
  type: "cfamm" | "clob";
  label: string;
  tokens: string[];
}

export interface WorldMarketLink {
  from: string;
  to: string;
  token: string;
}

export interface WorldSpec {
  markets: WorldMarketBlock[];
  links: WorldMarketLink[];
}

/**
 * US-007: LST exchange-rate drift parameters. Mirrors the Python
 * `ExchangeRateDriftSpec` (src/defi_sim/engine/specs.py:179). Surfaced
 * in the builder as part of the per-token Extensions panel.
 */
export interface ExchangeRateDriftSpec {
  drift_per_epoch: number;
  volatility_per_epoch: number;
  seed?: number | null;
}

/**
 * US-007: Token-2022 transfer-hook overhead spec. Mirrors the Python
 * `TransferHookSpec` (src/defi_sim/engine/specs.py:209). Engine adds the
 * configured CU + lamport overhead per transfer when `program_id` is set.
 */
export interface TransferHookSpec {
  program_id?: string | null;
  additional_cu_per_transfer: number;
  additional_lamports_per_transfer: number;
}

export interface MarketTokenSpec {
  id: string;
  symbol: string;
  decimals: number;
  native?: boolean;
  standard?: "native" | "spl" | "spl_2022";
  /**
   * US-007 extension: LST redemption rate to SOL. Numeric form on the
   * wire — backend coerces via `Decimal(str(rate))`. `null` for non-LSTs.
   */
  exchange_rate_to_sol?: number | string | null;
  exchange_rate_drift?: ExchangeRateDriftSpec | null;
  transfer_hook?: TransferHookSpec | null;
  /** US-007 PRD line 580: SPL-2022 confidential-transfer flag (stub). */
  confidential?: boolean;
}

/**
 * US-012 PRD line 974: validator-set entry surfaced in the builder under
 * the Solana execution panel. Mirrors `ValidatorParams` in
 * `defi_sim.agents.validator`. `client="jito_solana"` validators capture
 * tip revenue minus `stake_pool_share`; `client="vanilla"` validators
 * forgo MEV revenue entirely.
 */
export interface ValidatorSetEntry {
  pubkey: string;
  client: "jito_solana" | "vanilla";
  stake_lamports: number;
  stake_pool_share: number;
  stake_pool_address?: string | null;
  commission_pct?: number;
}

export interface RunSpec {
  market: {
    type: MarketType;
    num_assets: number;
    initial_liquidity: number;
    token_decimals: number;
    /**
     * Explicit per-token list preserved from the API/template payload. When
     * present, adapters must round-trip this verbatim (preserving SOL/USDC
     * decimals, native/standard flags, etc.) instead of regenerating
     * YES/NO/COLLATERAL from scalars. Absent on legacy specs that pre-date
     * the Solana pivot — adapters fall back to the scalar synthesis path.
     */
    tokens?: MarketTokenSpec[];
    /**
     * Quote/collateral token ID preserved from the API payload. When
     * present, adapters emit it verbatim instead of falling back to
     * `"COLLATERAL"`. Absent on legacy specs.
     */
    collateral_token_id?: string;
    /**
     * Whirlpool-only protocol variables surfaced in the builder's
     * "Protocol variables" tab. Round-tripped into `market.params` on
     * the wire — backend rejects whirlpool specs without `corpus_slot`
     * and `pool_pubkey` (defi_sim/engine/specs.py whirlpool guard).
     */
    whirlpool_params?: {
      corpus_slot?: number;
      pool_pubkey?: string;
      pool_account_id?: string;
      token_a_id?: string;
      token_b_id?: string;
      token_a_symbol?: string;
      token_b_symbol?: string;
    };
  };
  world?: WorldSpec;
  clock: {
    type: ClockType;
    block_time: number;
    epoch_length: number;
    /**
     * Solana-only: per-slot skip probability (0.0 — 1.0). Surfaced in
     * the builder when `type === "solana_slot"`; round-tripped to the
     * backend as `params.skip_rate`. Absent on non-Solana clocks.
     */
    skip_rate?: number;
  };
  execution: {
    model: ExecutionModel;
    ordering: OrderingModel;
    cost_model: CostModel;
    /**
     * US-003: scheduler discriminator. `serial` reproduces today's
     * single-lane behaviour; `priority` enables the parallel
     * conflict-graph scheduler (default for Solana).
     */
    scheduler?: "serial" | "priority";
    /**
     * Solana-only (US-002): per-tx / per-slot / per-writable-account compute
     * unit caps. Defaults match current mainnet (60M / 1.4M / 12M). Surfaced
     * in the builder when `model === "solana"`; round-tripped to the backend
     * as `execution.params.compute_budget`.
     */
    compute_budget?: {
      preset?: string;
      per_slot: number;
      per_tx: number;
      per_writable_account: number;
    };
    /**
     * Solana-only (US-004): per-submission-path landing-probability priors.
     * `calibrated_at === null` (default) marks the priors as synthetic and
     * surfaces a warning badge in the builder.
     */
    submission_priors?: {
      rpc_landing_prob_baseline: number;
      tpu_quic_landing_prob_baseline: number;
      jito_relayer_landing_prob_baseline: number;
      congestion_penalty_per_pct_full: number;
      calibrated_at: string | null;
    };
    /**
     * Solana-only (US-006): canonical oracle source for the run. The
     * builder picker maps to one of the Python presets in
     * `defi_sim.engine.oracles.presets` (Pyth Pull, Pyth Lazer,
     * Switchboard On-Demand). `none` skips the surcharge path.
     */
    oracle_preset?: "none" | "pyth_pull" | "pyth_lazer" | "switchboard_on_demand";
    /**
     * Solana-only (US-010, PRD line 747): per-account priority-fee market
     * tuning. Mirrors `PriorityFeeMarketSpec`; round-tripped to the backend
     * as `execution.params.priority_fee_market`. Surfaced in the builder
     * as an advanced collapsible section under the Solana execution panel.
     */
    priority_fee_market?: {
      window_slots: number;
      ewma_half_life_slots: number;
      floor_micro_lamports: number;
      update_event_threshold: number;
      /**
       * US-001 / lighthouse PRD line 58: pre-roll seeds the per-account
       * priority-fee distribution before slot 0 so the JitoSearcher gets
       * a realistic percentile target instead of the floor on its first
       * sandwich attempt.
       */
      pre_roll?: {
        slots: number;
        accounts: string[];
        cu_price_min: number;
        cu_price_max: number;
        observations_per_slot: number;
        seed: number;
      };
    };
    /**
     * Solana-only (lighthouse): per-slot bundle-auction config.
     * Round-tripped to the backend as `execution.params.bundle_auction`.
     */
    bundle_auction?: {
      max_bundles_per_slot: number;
      jito_stake_pool_share: number;
      tip_quote_curve_path?: string;
    };
    /**
     * Solana-only: identifies the cost token used to score bundle EV.
     * Defaults to USDC; non-lighthouse runs leave it unset.
     */
    cost_token?: string;
    /**
     * Solana-only: the bundle auction's visibility allow-list. Roles
     * outside this list see no bundle book on a given slot.
     */
    visible_roles?: string[];
    /**
     * Solana-only (US-012, PRD line 974): validator set under the Solana
     * execution panel. Mirrors `ValidatorParams` from
     * `defi_sim.agents.validator`. Default seed: one Jito-Solana validator
     * at 100% stake. Round-tripped on `execution.validator_set` so loaded
     * runs preserve the user's per-validator client / revenue-share config.
     */
    validator_set?: ValidatorSetEntry[];
    /**
     * Solana-only (US-014, PRD line 1109 / line 1125): adversarial fork
     * configuration. `fork_probability_per_slot` defaults to 0 (no forks);
     * `max_reorg_depth_slots` bounds the rolling reorg buffer. Round-tripped
     * to the backend as `execution.params.fork_spec` so a loaded run preserves
     * the user's fork-stress settings.
     */
    fork_spec?: {
      fork_probability_per_slot: number;
      max_reorg_depth_slots: number;
      seed?: number | null;
    };
  };
  fee_model: {
    type: FeeModel;
    rate_bps: number;
  };
  agents: {
    total: number;
    mix: AgentMix;
    default_collateral: number;
    role_params?: Partial<Record<RoleKey, RoleParams>>;
    /**
     * Dynamic group list (US-012). Preferred source of truth when
     * present — the adapter emits backend agents straight from these
     * entries. When absent, the legacy `mix` / `role_params` path is
     * used so older templates and fetched runs still round-trip.
     */
    groups?: AgentGroup[];
  };
  feeds: {
    type: string;
    process: string;
    drift: number;
    volatility: number;
    initial_price: number;
  }[];
  config: {
    num_rounds: number;
    snapshot_interval: number;
    seed: number;
    numeric_mode: NumericMode;
    information_filter: string;
  };
  /**
   * Solana-only (lighthouse): top-level Address Lookup Tables. Stored
   * verbatim on the wire under `spec.alts` so jito_searcher agents can
   * reference them by `id`.
   */
  alts?: Array<{ id: string; entries: string[] }>;
}

export interface SimRun {
  id: string;
  name: string;
  market: string;
  agents: number;
  currentRound: number;
  totalRounds: number;
  status: SimStatus;
  seed: number;
  exec: string;
  ordering: string;
  fee: string;
  feed: string;
  createdAt: string;
  spec: RunSpec;
  /**
   * Calibration metadata derived from the backend run summary.
   * `isCalibratedReplay` is `summary.mainnet_accuracy_claim` (PRD US-002 line 338).
   * Drives the studio overlay (PRD US-004 line 781).
   */
  calibration?: {
    isCalibratedReplay: boolean;
    replayKind?: string;
  };
}

export interface AgentRow {
  id: number;
  /** Backend agent identifier (e.g. "noise-1"). Falls back to `String(id)` when not available. */
  agentId: string;
  role: string;
  balance: number;
  volume: number;
  /**
   * Run-cumulative swap volume in raw quote-token units. Populated by
   * markets that can attribute each swap to a single quote side (whirlpool:
   * token B). Zero / undefined for markets that don't, in which case the
   * UI falls back to `volume` (the mixed-decimal raw sum).
   */
  volumeQuote?: number;
  pnl: number;
  trades: number;
  /** Per-token balance map (raw base-units) when available from the result. */
  balances?: Record<string, number>;
}

export interface EvEntry {
  round: number;
  evType: string;
  cls: string;
  detail: string;
  data?: Record<string, unknown>;
}

export type EventFilter = "all" | "trade" | "lp" | "fail";

export interface SimMetrics {
  // Engine-derived metrics; null when the engine couldn't compute them
  // (insufficient data, no priced market, no searcher, etc.).
  klDivergence: number | null;
  convergenceSpeed: number | null;
  // ``null`` when neither fee_history nor a usable liquidity-history fallback
  // is available (the placeholder ``1`` was previously rendered as 1.000 on
  // the dashboard, which lied — the value was the divide-by-zero default,
  // not a real fee yield).
  lpProfitability: number | null;
  manipulationCost: number | null;
  maxDrawdown: number;
  rollingVol: number;
  twap: number;
  slippage: number | null;
  exitability: number | null;
  compositeScore: number;
  // Manipulation-stress score (0–100). Mirrors compositeScore's shape but
  // measures attacker success rather than market health: high = searchers
  // are landing many sandwiches and extracting EV. Useful as a counterpart
  // to compositeScore on sandwich-attack templates where "the market looks
  // healthy" and "searchers are draining LPs" can both be true at once.
  stressScore: number;
  // Raw inputs feeding stressScore — surfaced so the UI can show the
  // "X bundles landed in Y slots, extracted Z lamports" subtext without
  // recomputing from round_snapshots.
  sandwichBundlesLanded: number;
  sandwichBundlesSubmitted: number;
  sandwichRealizedEvLamports: number;
  // Whirlpool/CLMM-only counters. ``tickCrossings`` totals across the run
  // (how many initialized ticks swaps consumed). Zero on non-Whirlpool runs.
  tickCrossings: number;
}

