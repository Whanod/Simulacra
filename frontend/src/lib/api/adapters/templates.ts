import type {
  AgentMix,
  MarketTokenSpec,
  RunSpec,
} from "@/lib/types/simulations";

export interface ApiTemplate {
  template_id: string;
  name: string;
  description?: string;
  base_spec: Record<string, unknown>;
  editable_fields?: string[];
  recommended_metrics?: string[];
  synthetic_mode?: boolean;
  synthetic_math_model?: string | null;
  non_transferable_conclusions?: string[];
  featured?: boolean;
}

export interface ApiTemplatesResponse {
  templates: ApiTemplate[];
  count: number;
}

export interface SimTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  spec: Partial<RunSpec>;
  // Full backend base_spec, preserved verbatim. The structured form
  // is lossy (collapses agents to a mix, drops alts, execution.params,
  // jito_searcher tip_curve, etc.); rawSpec is the source of truth so
  // raw mode and Build & Run can recover those fields when the
  // structured form would otherwise discard them.
  rawSpec: Record<string, unknown>;
  editableFields: string[];
  recommendedMetrics: string[];
  syntheticMode: boolean;
  syntheticMathModel: string | null;
  nonTransferableConclusions: string[];
  featured: boolean;
  // True when the structured form cannot represent the template
  // (e.g. lighthouse: ALTs, jito_searcher tip_curve, bundle_auction).
  // Callers should drive raw mode for these.
  requiresRawSpec: boolean;
}

function deepClone<T>(value: T): T {
  if (typeof structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value)) as T;
}

function detectRequiresRawSpec(base: Record<string, unknown>): boolean {
  if (Array.isArray(base.alts) && base.alts.length > 0) return true;
  const exec = base.execution as { params?: unknown } | undefined;
  if (exec && typeof exec === "object" && exec.params && typeof exec.params === "object") {
    return true;
  }
  const agents = Array.isArray(base.agents) ? base.agents : [];
  for (const a of agents) {
    const t = (a as { type?: string })?.type;
    if (t === "jito_searcher") return true;
    const params = (a as { params?: Record<string, unknown> })?.params;
    if (params && typeof params === "object") {
      for (const v of Object.values(params)) {
        if (v && typeof v === "object") return true;
      }
    }
  }
  return false;
}

interface ApiBaseSpec {
  market?: {
    type?: string;
    tokens?: Array<{
      id?: string;
      symbol?: string;
      decimals?: number;
      native?: boolean;
      standard?: string;
    }>;
    fee_model?: { type?: string; params?: { trade_fee_bps?: number } };
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
    markets?: Record<string, unknown>;
  };
  agents?: Array<{
    type?: string;
    initial_balances?: Record<string, number>;
  }>;
  execution?: {
    type?: string;
    ordering?: { type?: string };
    gas_model?: { type?: string };
  };
  num_rounds?: number;
  snapshot_interval?: number;
  seed?: number;
}

const DEFAULT_TEMPLATE_AGENT_TOTAL = 40;

function categoryFromId(id: string): string {
  if (id.includes("fee")) return "Market Design";
  if (id.includes("mev") || id.includes("stress")) return "Security";
  if (id.includes("lp") || id.includes("liquidity")) return "Liquidity";
  if (id.includes("cross") || id.includes("arbitrage") || id.includes("world")) {
    return "Multi-Market";
  }
  return "General";
}

function mixFromAgents(agents: ApiBaseSpec["agents"]): AgentMix {
  const counts: Record<keyof AgentMix, number> = {
    noise: 0,
    informed: 0,
    arbitrageur: 0,
    manipulator: 0,
    passive_lp: 0,
    rebalancing_lp: 0,
  };
  const aliases: Record<string, keyof AgentMix> = {
    noise: "noise",
    informed: "informed",
    arbitrageur: "arbitrageur",
    arb: "arbitrageur",
    manipulator: "manipulator",
    lp: "passive_lp",
    passive_lp: "passive_lp",
    rebalancing_lp: "rebalancing_lp",
  };
  if (!Array.isArray(agents) || agents.length === 0) {
    return {
      noise: 0.4,
      informed: 0.2,
      arbitrageur: 0.15,
      manipulator: 0.05,
      passive_lp: 0.15,
      rebalancing_lp: 0.05,
    };
  }
  for (const a of agents) {
    const key = aliases[(a?.type || "").toLowerCase()];
    if (key) counts[key] += 1;
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

function execModelFromApi(t: string | undefined): RunSpec["execution"]["model"] {
  if (!t) return "direct";
  if (t === "solana_like") return "solana";
  return t;
}

function orderingFromApi(
  t: string | undefined,
): RunSpec["execution"]["ordering"] {
  const valid: RunSpec["execution"]["ordering"][] = [
    "fifo",
    "random",
    "priority",
    "sandwich",
    "block_builder",
  ];
  return (valid as string[]).includes(t || "")
    ? (t as RunSpec["execution"]["ordering"])
    : "fifo";
}

function costModelFromApi(
  t: string | undefined,
): RunSpec["execution"]["cost_model"] {
  const valid: RunSpec["execution"]["cost_model"][] = [
    "zero",
    "fixed",
    "typed",
    "eip1559",
    "compute_unit",
  ];
  return (valid as string[]).includes(t || "")
    ? (t as RunSpec["execution"]["cost_model"])
    : "zero";
}

function marketTypeFromApi(t: string | undefined): RunSpec["market"]["type"] {
  return t ?? "cfamm";
}

type ApiTokenInput = NonNullable<NonNullable<ApiBaseSpec["market"]>["tokens"]>;

function normalizeMarketTokens(
  raw: ApiTokenInput | undefined,
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
    out.push(entry);
  }
  return out.length > 0 ? out : undefined;
}

export function templateFromApi(raw: ApiTemplate): SimTemplate {
  const base = (raw.base_spec || {}) as ApiBaseSpec;
  const market = base.market || {};
  const tokens = Array.isArray(market.tokens) ? market.tokens : [];
  const partial: Partial<RunSpec> = {};

  const normalizedTokens = normalizeMarketTokens(market.tokens);
  const collateralTokenId =
    typeof market.params?.collateral_token === "string"
      ? market.params.collateral_token
      : undefined;
  const whirlpoolParams =
    market.type === "whirlpool" && market.params
      ? {
          ...(typeof market.params.corpus_slot === "number"
            ? { corpus_slot: market.params.corpus_slot }
            : {}),
          ...(typeof market.params.pool_pubkey === "string"
            ? { pool_pubkey: market.params.pool_pubkey }
            : {}),
          ...(typeof market.params.pool_account_id === "string"
            ? { pool_account_id: market.params.pool_account_id }
            : {}),
          ...(typeof market.params.token_a_id === "string"
            ? { token_a_id: market.params.token_a_id }
            : {}),
          ...(typeof market.params.token_b_id === "string"
            ? { token_b_id: market.params.token_b_id }
            : {}),
          ...(typeof market.params.token_a_symbol === "string"
            ? { token_a_symbol: market.params.token_a_symbol }
            : {}),
          ...(typeof market.params.token_b_symbol === "string"
            ? { token_b_symbol: market.params.token_b_symbol }
            : {}),
        }
      : undefined;
  partial.market = {
    type: marketTypeFromApi(market.type),
    num_assets: Math.max(tokens.length, 2),
    initial_liquidity:
      typeof market.params?.initial_liquidity === "number"
        ? market.params.initial_liquidity
        : 1_000_000,
    token_decimals:
      typeof tokens[0]?.decimals === "number" ? tokens[0].decimals : 9,
    ...(normalizedTokens ? { tokens: normalizedTokens } : {}),
    ...(collateralTokenId ? { collateral_token_id: collateralTokenId } : {}),
    ...(whirlpoolParams && Object.keys(whirlpoolParams).length > 0
      ? { whirlpool_params: whirlpoolParams }
      : {}),
  };

  const feeType = market.fee_model?.type;
  const feeBps = market.fee_model?.params?.trade_fee_bps;
  partial.fee_model = {
    type: (feeType as RunSpec["fee_model"]["type"]) || "flat",
    rate_bps: typeof feeBps === "number" ? feeBps : 30,
  };

  if (base.execution) {
    partial.execution = {
      model: execModelFromApi(base.execution.type),
      ordering: orderingFromApi(base.execution.ordering?.type),
      cost_model: costModelFromApi(base.execution.gas_model?.type),
    };
  }

  const agents = Array.isArray(base.agents) ? base.agents : [];
  const defaultCollateral = (() => {
    const first = agents[0];
    if (first?.initial_balances) {
      const vals = Object.values(first.initial_balances).filter(
        (v) => typeof v === "number",
      );
      if (vals.length > 0) return Math.min(...vals);
    }
    return 100_000;
  })();
  partial.agents = {
    // Backend templates list representative example agents. In the builder we
    // want those examples to define the role mix, not collapse the population
    // size to 1-2 agents unless the user manually fixes it.
    total: Math.max(agents.length, DEFAULT_TEMPLATE_AGENT_TOTAL),
    mix: mixFromAgents(agents),
    default_collateral: defaultCollateral,
  };

  partial.config = {
    num_rounds: typeof base.num_rounds === "number" ? base.num_rounds : 200,
    snapshot_interval:
      typeof base.snapshot_interval === "number" ? base.snapshot_interval : 10,
    seed: typeof base.seed === "number" ? base.seed : 42,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  };

  return {
    id: raw.template_id,
    name: raw.name,
    description: raw.description || "",
    category: categoryFromId(raw.template_id),
    spec: partial,
    editableFields: Array.isArray(raw.editable_fields)
      ? raw.editable_fields
      : [],
    recommendedMetrics: Array.isArray(raw.recommended_metrics)
      ? raw.recommended_metrics
      : [],
    syntheticMode: raw.synthetic_mode === true,
    syntheticMathModel:
      typeof raw.synthetic_math_model === "string"
        ? raw.synthetic_math_model
        : null,
    nonTransferableConclusions: Array.isArray(raw.non_transferable_conclusions)
      ? raw.non_transferable_conclusions.filter(
          (s): s is string => typeof s === "string" && s.length > 0,
        )
      : [],
    featured: raw.featured === true,
    rawSpec: deepClone((raw.base_spec || {}) as Record<string, unknown>),
    requiresRawSpec: detectRequiresRawSpec(
      (raw.base_spec || {}) as Record<string, unknown>,
    ),
  };
}

export function fromApiTemplates(raws: ApiTemplate[]): SimTemplate[] {
  return raws.map(templateFromApi);
}
