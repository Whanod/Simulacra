import type { RegistryEntry } from "@/lib/types/registry";

export interface RegistryLabel {
  key: string;
  label: string;
}

export const CATEGORY_LABELS: Record<string, RegistryLabel> = {
  markets: { key: "reg-markets", label: "Markets" },
  agents: { key: "reg-agents", label: "Agents" },
  clocks: { key: "reg-clocks", label: "Clocks" },
  orderings: { key: "reg-ordering", label: "Ordering" },
  gas_models: { key: "reg-gas", label: "Cost Models" },
  fee_models: { key: "reg-fees", label: "Fee Models" },
  feeds: { key: "reg-feeds", label: "Feeds" },
  execution_models: { key: "reg-exec", label: "Execution" },
  information_filters: { key: "reg-information", label: "Information" },
};

export const CATEGORY_ORDER: string[] = [
  "markets",
  "agents",
  "clocks",
  "orderings",
  "gas_models",
  "fee_models",
  "feeds",
  "execution_models",
  "information_filters",
];

// US-016: stubs omit both `name` (supplied from backend label) and
// `type` (keyed by the outer record, so storing it again would be
// duplicative and drift-prone).
type EntryStub = Omit<RegistryEntry, "name" | "type">;

/**
 * Optional description overlay for known backend types. Unknown types
 * fall back to a minimal entry (title-cased name, empty description)
 * produced by describeType. New backend types do not require entries
 * here — this file is an enrichment source, not a coverage contract.
 */
export const DESCRIPTIONS: Record<string, Record<string, EntryStub>> = {
  markets: {
    cfamm: {
      description:
        "L²-norm constant-function AMM with LP position tracking and multi-asset support.",
      badges: [
        { label: "PricedMarket", variant: "blue" },
        { label: "LiquidityPool", variant: "purple" },
      ],
    },
    clob: {
      description:
        "Central limit order book with price-time priority matching and per-pair books.",
      badges: [
        { label: "PricedMarket", variant: "blue" },
        { label: "OrderBook", variant: "green" },
      ],
    },
    world: {
      description:
        "Composite world market — run multiple markets in one simulation with cross-market agents.",
      badges: [{ label: "Composite", variant: "yellow" }],
    },
  },
  agents: {
    noise: {
      description:
        "Random trades within configurable size/frequency bounds. Provides background liquidity.",
      params: "trade_min, trade_max, frequency, bundle_probability",
    },
    informed: {
      description:
        "Trades toward belief distribution weighted by conviction. Bundle-based execution.",
      params: "conviction, trade_fraction, capital_limit",
    },
    arbitrageur: {
      description:
        "Exploits price differences between market and feed. Corrective positions.",
      params: "price_tolerance, max_position_size, rebalance_interval",
    },
    manipulator: {
      description:
        "Strategic price manipulation with attack budgets. Measures attack success.",
      params: "attack_capital, target_price_move, execution_window",
    },
    lp: {
      description: "Generic liquidity provider role shared across AMM/CLOB markets.",
    },
    passive_lp: {
      description:
        "Deposits if yield attractive, withdraws if loss exceeds threshold.",
      params: "deposit_fraction, min_yield, max_loss, rebalance_interval",
    },
    rebalancing_lp: {
      description:
        "Maintains uniform portfolio weights across assets with periodic rebalancing.",
      params: "deposit_fraction, rebalance_interval",
    },
  },
  clocks: {
    block: {
      description: "Fixed block time with deterministic round progression.",
      badges: [{ label: "Default", variant: "green" }],
    },
    variable_block: {
      description:
        "Variable block interval sampled per round from a distribution.",
    },
  },
  orderings: {
    fifo: {
      description: "First-in, first-out arrival order. Default for direct execution.",
    },
    random: { description: "RNG-based shuffle. Eliminates ordering bias." },
    priority: {
      description:
        "Sorted by compute-unit priority lamports (price × CU limit) descending. Models the Solana priority fee market.",
    },
    sandwich: {
      description: "Front-run / back-run sandwich attack patterns. MEV simulation.",
    },
    block_builder: {
      description:
        "Custom MEV builder strategy with configurable extraction.",
    },
  },
  gas_models: {
    zero: { description: "No transaction costs. Default." },
    fixed: { description: "Constant cost per action regardless of type." },
    typed: {
      description:
        "Per-action-type cost schedule. Different costs for swaps vs. LP vs. orders.",
    },
    eip1559: {
      description: "Base fee + priority fee with dynamic adjustment.",
    },
    compute_unit: {
      description:
        "Solana mainnet fee formula: 5,000 lamports per signer plus ceil(compute-unit price × CU limit / 1,000,000).",
    },
  },
  fee_models: {
    flat: { description: "Fixed percentage fee on trade volume." },
    dynamic: { description: "Fee rate adjusts based on market conditions." },
    tiered: { description: "Volume-based fee tiers with breakpoints." },
    spread: { description: "Fee proportional to bid-ask spread." },
    time_weighted: { description: "Fee varies based on time since last trade." },
  },
  feeds: {
    stochastic: {
      description:
        "Stochastic process feeds — GBM, mean-reversion, jump diffusion, configurable per token.",
    },
    historical: {
      description: "Replay from arrays, CSV, or Parquet files. Deterministic.",
    },
    composite: {
      description:
        "Combine multiple feed types per token. Weighted or fallback.",
    },
  },
  execution_models: {
    direct: {
      description:
        "Network-neutral default. FIFO ordering, zero cost, no queue visibility. Suitable for protocol-level analysis without network effects.",
      badges: [{ label: "Default", variant: "green" }],
    },
    batch: {
      description:
        "Composable with queue visibility and admission policies. Supports custom ordering and cost models.",
    },
    solana_like: {
      description:
        "Compute-unit pricing with priority fees and fast finality. Solana mainnet fee model.",
      badges: [{ label: "Compute Unit", variant: "purple" }],
    },
  },
  information_filters: {
    full_transparency: {
      description:
        "Agents see complete market state. Default for simulations without information asymmetry.",
      badges: [{ label: "Default", variant: "green" }],
    },
    delayed_information: {
      description:
        "Agents receive state with configurable round delay. Models stale book feeds.",
    },
  },
};

export function titleCase(value: string): string {
  return value
    .split(/[\s_-]+/)
    .map((word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : ""))
    .join(" ");
}

export function labelForCategory(backendKey: string): RegistryLabel {
  return (
    CATEGORY_LABELS[backendKey] ?? {
      key: `reg-${backendKey}`,
      label: titleCase(backendKey),
    }
  );
}

export function describeType(category: string, type: string): RegistryEntry {
  const stub = DESCRIPTIONS[category]?.[type.toLowerCase()];
  const base: RegistryEntry = {
    name: titleCase(type),
    type,
    description: stub?.description ?? "",
  };
  if (stub?.params !== undefined) base.params = stub.params;
  if (stub?.badges !== undefined) base.badges = stub.badges;
  if (stub?.disabled !== undefined) base.disabled = stub.disabled;
  return base;
}
