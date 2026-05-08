import { describe, expect, it } from "vitest";

import {
  specFromApi,
  specToApi,
  type ApiRunSpec,
} from "@/lib/api/adapters/runs";

/**
 * Mirror of the `solana-sandwich-lighthouse` `base_spec` agents +
 * supporting blocks (src/defi_sim_api/backend/templates.py:282–506).
 * Trimmed to the fields WS-1 covers: agents (with per-agent params,
 * agent_id stems and initial_balances), and the surrounding spec
 * scaffolding the adapter needs to produce a valid RunSpec. WS-2
 * extends this fixture to carry alts / bundle_auction / pre-roll.
 */
const LIGHTHOUSE_RAW: ApiRunSpec = {
  market: {
    type: "whirlpool",
    tokens: [
      { id: "SOL", symbol: "SOL", decimals: 9, native: true, standard: "native" },
      { id: "USDC", symbol: "USDC", decimals: 6, standard: "spl" },
    ],
    params: {
      initial_liquidity: 1_000_000,
      collateral_token: "USDC",
      corpus_slot: 417595698,
      pool_pubkey: "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
      pool_account_id: "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
      token_a_id: "SOL",
      token_b_id: "USDC",
      token_a_symbol: "SOL",
      token_b_symbol: "USDC",
    },
  },
  clock: {
    type: "solana_slot",
    params: {
      slot_duration_seconds: 0.4,
      epoch_length_slots: 432_000,
      skip_rate: 0,
    },
  },
  execution: {
    type: "solana_like",
    ordering: { type: "priority" },
    gas_model: { type: "compute_unit", params: {} },
    params: {
      cost_token: "USDC",
      visible_roles: ["jito_searcher"],
      compute_budget: {
        per_slot: 1_200_000,
        per_tx: 1_400_000,
        per_writable_account: 600_000,
      },
      priority_fee_market: {
        window_slots: 150,
        ewma_half_life_slots: 30,
        floor_micro_lamports: 1,
        update_event_threshold: 0.001,
        pre_roll: {
          slots: 200,
          accounts: ["HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"],
          cu_price_min: 1_000,
          cu_price_max: 50_000,
          observations_per_slot: 2,
          seed: 1337,
        },
      },
      bundle_auction: {
        max_bundles_per_slot: 5,
        jito_stake_pool_share: 0.05,
        tip_quote_curve_path: "solana-plans/calibration/jito_tip_curves.yaml",
      },
    },
  },
  alts: [
    {
      id: "alt-whirlpool-sol-usdc",
      entries: [
        "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
        "11111111111111111111111111111111",
      ],
    },
  ],

  default_fee_model: { type: "flat", params: { trade_fee_bps: 30 } },
  agents: [
    {
      type: "noise",
      agent_id: "noise-1",
      params: {
        collateral: "USDC",
        frequency: 0.25,
        bundle_probability: 0,
        trade_min: 100_000,
        trade_max: 5_000_000,
      },
      initial_balances: { USDC: 500_000_000 },
    },
    {
      type: "noise",
      agent_id: "noise-2",
      params: {
        collateral: "USDC",
        frequency: 0.25,
        bundle_probability: 0,
        trade_min: 100_000,
        trade_max: 5_000_000,
      },
      initial_balances: { USDC: 500_000_000 },
    },
    {
      type: "noise",
      agent_id: "noise-3",
      params: {
        collateral: "USDC",
        frequency: 0.25,
        bundle_probability: 0,
        trade_min: 100_000,
        trade_max: 5_000_000,
      },
      initial_balances: { USDC: 500_000_000 },
    },
    {
      type: "noise",
      agent_id: "noise-4",
      params: {
        collateral: "USDC",
        frequency: 0.25,
        bundle_probability: 0,
        trade_min: 100_000,
        trade_max: 5_000_000,
      },
      initial_balances: { USDC: 500_000_000 },
    },
    {
      type: "swap_noise",
      agent_id: "victim-1",
      params: {
        token_in: "USDC",
        token_out: "SOL",
        amount_min: 500_000,
        amount_max: 25_000_000,
        frequency: 0.5,
        cu_price_min: 1_000,
        cu_price_max: 80_000,
      },
      initial_balances: { USDC: 1_000_000_000, SOL: 10_000_000_000 },
    },
    {
      type: "swap_noise",
      agent_id: "victim-small",
      params: {
        token_in: "USDC",
        token_out: "SOL",
        amount_min: 50_000,
        amount_max: 1_500_000,
        frequency: 0.7,
        cu_price_min: 100,
        cu_price_max: 30_000,
      },
      initial_balances: { USDC: 500_000_000, SOL: 5_000_000_000 },
    },
    {
      type: "manipulator",
      agent_id: "sandwich-1",
      params: {
        collateral: "USDC",
        budget: 50_000_000,
        num_tranches: 50,
        spend_fraction: 0.01,
      },
      initial_balances: { USDC: 500_000_000 },
    },
    {
      type: "passive_lp",
      agent_id: "lp-1",
      params: { collateral: "USDC" },
      initial_balances: { USDC: 2_000_000_000 },
    },
    {
      type: "jito_searcher",
      agent_id: "searcher-1",
      params: {
        strategies: ["sandwich"],
        tip_curve: { kind: "linear", slope_micro_lamports_per_ev: 0.05 },
        min_ev_to_submit_lamports: 3_000_000,
        tip_account: "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        max_bundle_size: 5,
        priority_fee_percentile_target: 75,
        alt_ids: ["alt-whirlpool-sol-usdc"],
      },
      initial_balances: { USDC: 25_000_000, SOL: 25_000_000 },
    },
  ],
  num_rounds: 500,
  snapshot_interval: 1,
  seed: 1337,
  numeric_mode: "fixed",
};

describe("lighthouse round trip — agent fidelity", () => {
  it("preserves per-agent params and initial_balances through specFromApi → specToApi", () => {
    const spec = specFromApi(LIGHTHOUSE_RAW);

    expect(spec.agents.groups).toBeDefined();
    const groups = spec.agents.groups!;
    // 4 noise (coalesced), 1 victim-1, 1 victim-small, 1 sandwich-1,
    // 1 lp-1, 1 searcher-1 → 6 distinct groups.
    expect(groups.length).toBe(6);

    const noise = groups.find((g) => g.type === "noise");
    expect(noise?.count).toBe(4);
    expect(noise?.agentIdPrefix).toBe("noise");
    expect(noise?.params.frequency).toBe(0.25);
    expect(noise?.initialBalances).toEqual({ USDC: 500_000_000 });

    const victim1 = groups.find((g) => g.agentIdPrefix === "victim-1");
    expect(victim1?.type).toBe("swap_noise");
    expect(victim1?.count).toBe(1);
    expect(victim1?.params.amount_max).toBe(25_000_000);
    expect(victim1?.initialBalances).toEqual({
      USDC: 1_000_000_000,
      SOL: 10_000_000_000,
    });

    const searcher = groups.find((g) => g.type === "jito_searcher");
    expect(searcher?.agentIdPrefix).toBe("searcher-1");
    expect((searcher?.params.tip_curve as Record<string, unknown>)?.slope_micro_lamports_per_ev).toBe(0.05);
    expect(searcher?.params.min_ev_to_submit_lamports).toBe(3_000_000);
    expect(searcher?.initialBalances).toEqual({
      USDC: 25_000_000,
      SOL: 25_000_000,
    });
  });

  it("emits the same agent array shape via specToApi", () => {
    const spec = specFromApi(LIGHTHOUSE_RAW);
    const out = specToApi(spec) as { agents: typeof LIGHTHOUSE_RAW.agents };
    const original = LIGHTHOUSE_RAW.agents!;
    expect(out.agents).toBeDefined();
    expect(out.agents!.length).toBe(original.length);

    for (let i = 0; i < original.length; i++) {
      const got = out.agents![i];
      const want = original[i];
      expect(got.type).toBe(want.type);
      expect(got.agent_id).toBe(want.agent_id);
      // Adapter no longer force-injects `collateral` — it would crash
      // backend agents like swap_noise / jito_searcher whose dataclass
      // doesn't accept that kwarg. Trust whatever was in want.params.
      expect(got.params).toEqual(want.params);
      expect(got.initial_balances).toEqual(want.initial_balances);
    }
  });

  it("preserves priority_fee_market.pre_roll, bundle_auction, cost_token, visible_roles", () => {
    const spec = specFromApi(LIGHTHOUSE_RAW);

    expect(spec.execution.priority_fee_market?.pre_roll?.slots).toBe(200);
    expect(spec.execution.priority_fee_market?.pre_roll?.accounts).toEqual([
      "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
    ]);
    expect(spec.execution.bundle_auction?.tip_quote_curve_path).toBe(
      "solana-plans/calibration/jito_tip_curves.yaml",
    );
    expect(spec.execution.bundle_auction?.max_bundles_per_slot).toBe(5);
    expect(spec.execution.cost_token).toBe("USDC");
    expect(spec.execution.visible_roles).toEqual(["jito_searcher"]);

    expect(spec.execution.model).toBe("solana");
    const out = specToApi(spec);
    const exec = (out.execution as Record<string, unknown>).params as Record<string, unknown>;
    expect(exec.priority_fee_market).toEqual({
      window_slots: 150,
      ewma_half_life_slots: 30,
      floor_micro_lamports: 1,
      update_event_threshold: 0.001,
      pre_roll: {
        slots: 200,
        accounts: ["HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ"],
        cu_price_min: 1_000,
        cu_price_max: 50_000,
        observations_per_slot: 2,
        seed: 1337,
      },
    });
    expect(exec.bundle_auction).toEqual({
      max_bundles_per_slot: 5,
      jito_stake_pool_share: 0.05,
      tip_quote_curve_path: "solana-plans/calibration/jito_tip_curves.yaml",
    });
    expect(exec.cost_token).toBe("USDC");
    expect(exec.visible_roles).toEqual(["jito_searcher"]);
  });

  it("preserves top-level alts", () => {
    const spec = specFromApi(LIGHTHOUSE_RAW);
    expect(spec.alts).toBeDefined();
    expect(spec.alts!.length).toBe(1);
    expect(spec.alts![0].id).toBe("alt-whirlpool-sol-usdc");
    expect(spec.alts![0].entries).toContain(
      "HJPjoWUrhoZzkNfRpHuieeFk9WcZWjwy6PBjZ81ngndJ",
    );

    const out = specToApi(spec) as { alts?: Array<{ id: string; entries: string[] }> };
    expect(out.alts).toEqual(spec.alts);
  });

  it("uses single agent_id (no -1 suffix) when count === 1", () => {
    const spec = specFromApi(LIGHTHOUSE_RAW);
    const out = specToApi(spec) as { agents: Array<{ agent_id?: string }> };
    const ids = out.agents.map((a) => a.agent_id);
    expect(ids).toContain("victim-1");
    expect(ids).toContain("victim-small");
    expect(ids).toContain("sandwich-1");
    expect(ids).toContain("searcher-1");
    expect(ids).toContain("lp-1");
    // 4 noise — should be noise-1..noise-4 since count > 1
    expect(ids.filter((id) => id?.startsWith("noise-")).length).toBe(4);
  });
});
