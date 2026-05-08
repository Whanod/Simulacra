import { describe, it, expect } from "vitest";

import {
  fromApiTemplates,
  templateFromApi,
  type ApiTemplate,
} from "@/lib/api/adapters/templates";
import { specToApi } from "@/lib/api/adapters/runs";
import type { RunSpec } from "@/lib/types/simulations";

const AMM_FEE_TEMPLATE: ApiTemplate = {
  template_id: "amm-fee-tuning",
  name: "AMM fee tuning",
  description: "Explore trade fee settings for a CFAMM.",
  base_spec: {
    market: {
      type: "cfamm",
      tokens: [
        { id: "YES", symbol: "YES", decimals: 9 },
        { id: "NO", symbol: "NO", decimals: 9 },
      ],
      fee_model: { type: "flat", params: { trade_fee_bps: 30 } },
      params: { initial_liquidity: 1_000_000, collateral_token: "COLLATERAL" },
    },
    agents: [
      {
        type: "noise",
        agent_id: "noise-1",
        params: { collateral: "COLLATERAL", frequency: 0.2 },
        initial_balances: { COLLATERAL: 1_000_000_000 },
      },
    ],
    num_rounds: 20,
    snapshot_interval: 1,
    seed: 42,
  },
  editable_fields: [
    "market.fee_model.params.trade_fee_bps",
    "market.params.initial_liquidity",
    "agents[0].params.frequency",
    "num_rounds",
  ],
  recommended_metrics: ["final_yes_price", "num_rounds_executed"],
};

const MEV_TEMPLATE: ApiTemplate = {
  template_id: "mev-stress-test",
  name: "MEV stress test",
  description: "Adversarial execution.",
  base_spec: {
    market: {
      type: "cfamm",
      tokens: [
        { id: "YES", symbol: "YES", decimals: 9 },
        { id: "NO", symbol: "NO", decimals: 9 },
      ],
      params: { initial_liquidity: 2_000_000 },
    },
    agents: [
      {
        type: "manipulator",
        agent_id: "mev-1",
        params: { collateral: "COLLATERAL" },
        initial_balances: { COLLATERAL: 1_000_000_000 },
      },
    ],
    execution: {
      type: "batch",
      ordering: { type: "priority" },
      gas_model: { type: "fixed", params: { cost_per_action: 5 } },
    },
    num_rounds: 20,
    snapshot_interval: 1,
    seed: 99,
  },
};

const CROSS_MARKET_TEMPLATE: ApiTemplate = {
  template_id: "cross-market-arbitrage",
  name: "cross-market arbitrage",
  description: "AMM + CLOB world.",
  base_spec: {
    market: {
      type: "world",
      markets: {
        amm: { type: "cfamm" },
        book: { type: "clob" },
      },
    },
    agents: [
      {
        type: "arbitrageur",
        agent_id: "arb-1",
        initial_balances: { COLLATERAL: 1_000_000_000 },
      },
    ],
    num_rounds: 25,
    seed: 17,
  },
};

const EMPTY_TEMPLATE: ApiTemplate = {
  template_id: "bare-minimum",
  name: "bare minimum",
  base_spec: {},
};

describe("templates adapter", () => {
  describe("templateFromApi", () => {
    it("maps an AMM template to a partial RunSpec", () => {
      const tpl = templateFromApi(AMM_FEE_TEMPLATE);
      expect(tpl.id).toBe("amm-fee-tuning");
      expect(tpl.name).toBe("AMM fee tuning");
      expect(tpl.description).toContain("fee");
      expect(tpl.category).toBe("Market Design");
      expect(tpl.spec.market?.type).toBe("cfamm");
      expect(tpl.spec.market?.num_assets).toBe(2);
      expect(tpl.spec.market?.initial_liquidity).toBe(1_000_000);
      expect(tpl.spec.market?.token_decimals).toBe(9);
      expect(tpl.spec.fee_model?.type).toBe("flat");
      expect(tpl.spec.fee_model?.rate_bps).toBe(30);
      expect(tpl.spec.agents?.total).toBe(40);
      expect(tpl.spec.agents?.mix.noise).toBe(1);
      expect(tpl.spec.agents?.default_collateral).toBe(1_000_000_000);
      expect(tpl.spec.config?.num_rounds).toBe(20);
      expect(tpl.spec.config?.snapshot_interval).toBe(1);
      expect(tpl.spec.config?.seed).toBe(42);
      expect(tpl.editableFields).toContain("market.fee_model.params.trade_fee_bps");
      expect(tpl.recommendedMetrics).toContain("final_yes_price");
    });

    it("maps execution block when present", () => {
      const tpl = templateFromApi(MEV_TEMPLATE);
      expect(tpl.category).toBe("Security");
      expect(tpl.spec.execution?.model).toBe("batch");
      expect(tpl.spec.execution?.ordering).toBe("priority");
      expect(tpl.spec.execution?.cost_model).toBe("fixed");
      expect(tpl.spec.agents?.total).toBe(40);
      expect(tpl.spec.agents?.mix.manipulator).toBe(1);
    });

    it("handles world market type", () => {
      const tpl = templateFromApi(CROSS_MARKET_TEMPLATE);
      expect(tpl.category).toBe("Multi-Market");
      expect(tpl.spec.market?.type).toBe("world");
      expect(tpl.spec.agents?.total).toBe(40);
      expect(tpl.spec.agents?.mix.arbitrageur).toBe(1);
    });

    it("propagates synthetic_mode metadata when the API includes it", () => {
      const tpl = templateFromApi({
        ...AMM_FEE_TEMPLATE,
        synthetic_mode: true,
        synthetic_math_model: "l2_norm_cfamm",
        non_transferable_conclusions: [
          "Fee-tier rankings may flip on real Whirlpool CLMM.",
        ],
      });
      expect(tpl.syntheticMode).toBe(true);
      expect(tpl.syntheticMathModel).toBe("l2_norm_cfamm");
      expect(tpl.nonTransferableConclusions).toEqual([
        "Fee-tier rankings may flip on real Whirlpool CLMM.",
      ]);
    });

    it("defaults synthetic_mode to false when the API omits it", () => {
      const tpl = templateFromApi(AMM_FEE_TEMPLATE);
      expect(tpl.syntheticMode).toBe(false);
      expect(tpl.syntheticMathModel).toBeNull();
      expect(tpl.nonTransferableConclusions).toEqual([]);
    });

    it("propagates the featured flag when the API includes it", () => {
      expect(templateFromApi({ ...AMM_FEE_TEMPLATE, featured: true }).featured).toBe(
        true,
      );
      expect(templateFromApi(AMM_FEE_TEMPLATE).featured).toBe(false);
    });

    it("fills sensible defaults for a minimal template", () => {
      const tpl = templateFromApi(EMPTY_TEMPLATE);
      expect(tpl.spec.market?.type).toBe("cfamm");
      expect(tpl.spec.market?.num_assets).toBe(2);
      expect(tpl.spec.market?.initial_liquidity).toBe(1_000_000);
      expect(tpl.spec.market?.token_decimals).toBe(9);
      expect(tpl.spec.fee_model?.type).toBe("flat");
      expect(tpl.spec.fee_model?.rate_bps).toBe(30);
      expect(tpl.spec.agents?.total).toBe(40);
      expect(tpl.spec.config?.num_rounds).toBe(200);
      expect(tpl.spec.config?.seed).toBe(42);
      expect(tpl.spec.execution).toBeUndefined();
    });
  });

  describe("fromApiTemplates", () => {
    it("maps a list", () => {
      const list = fromApiTemplates([AMM_FEE_TEMPLATE, MEV_TEMPLATE]);
      expect(list).toHaveLength(2);
      expect(list[0].id).toBe("amm-fee-tuning");
      expect(list[1].id).toBe("mev-stress-test");
    });
  });

  describe("round_trip_preserves_sol_usdc_pair", () => {
    const SOLANA_TEMPLATE: ApiTemplate = {
      template_id: "whirlpool-fee-tuning",
      name: "Whirlpool fee tuning",
      base_spec: {
        market: {
          type: "cfamm",
          tokens: [
            { id: "SOL", symbol: "SOL", decimals: 9, native: true, standard: "native" },
            { id: "USDC", symbol: "USDC", decimals: 6, standard: "spl" },
          ],
          fee_model: { type: "flat", params: { trade_fee_bps: 30 } },
          params: { initial_liquidity: 1_000_000, collateral_token: "USDC" },
        },
        agents: [
          {
            type: "noise",
            agent_id: "noise-1",
            params: { collateral: "USDC", frequency: 0.2 },
            initial_balances: { USDC: 1_000_000_000 },
          },
        ],
        num_rounds: 10,
        seed: 7,
      },
    };

    it("preserves SOL/USDC tokens through templateFromApi → specToApi", () => {
      const tpl = templateFromApi(SOLANA_TEMPLATE);
      // Tokens are carried on the partial RunSpec.market, not collapsed to scalars.
      expect(tpl.spec.market?.tokens).toEqual([
        { id: "SOL", symbol: "SOL", decimals: 9, native: true, standard: "native" },
        { id: "USDC", symbol: "USDC", decimals: 6, standard: "spl" },
      ]);
      expect(tpl.spec.market?.collateral_token_id).toBe("USDC");

      // specToApi requires a full RunSpec; inflate the partial with sane
      // defaults (this mirrors what the studio store does on load).
      const fullSpec: RunSpec = {
        market: {
          type: tpl.spec.market!.type,
          num_assets: tpl.spec.market!.num_assets,
          initial_liquidity: tpl.spec.market!.initial_liquidity,
          token_decimals: tpl.spec.market!.token_decimals,
          tokens: tpl.spec.market!.tokens,
          collateral_token_id: tpl.spec.market!.collateral_token_id,
        },
        clock: { type: "block", block_time: 0.4, epoch_length: 432_000 },
        execution: { model: "solana_like", ordering: "fifo", cost_model: "compute_unit" },
        fee_model: { type: tpl.spec.fee_model!.type, rate_bps: tpl.spec.fee_model!.rate_bps },
        agents: tpl.spec.agents!,
        feeds: [
          {
            type: "stochastic",
            process: "gbm",
            drift: 0,
            volatility: 0.02,
            initial_price: 1,
          },
        ],
        config: tpl.spec.config!,
      };

      const api = specToApi(fullSpec);
      const market = api.market as {
        type: string;
        tokens: Array<{ id: string; symbol: string; decimals: number; native?: boolean; standard?: string }>;
        params: { collateral_token: string };
      };
      expect(market.type).toBe("cfamm");
      const symbols = market.tokens.map((t) => t.symbol).sort();
      expect(symbols).toEqual(["SOL", "USDC"]);
      const sol = market.tokens.find((t) => t.symbol === "SOL");
      const usdc = market.tokens.find((t) => t.symbol === "USDC");
      expect(sol?.decimals).toBe(9);
      expect(sol?.native).toBe(true);
      expect(sol?.standard).toBe("native");
      expect(usdc?.decimals).toBe(6);
      expect(usdc?.standard).toBe("spl");
      expect(market.params.collateral_token).toBe("USDC");
      // Critically, the YES/NO/COLLATERAL fallback path did NOT fire.
      expect(symbols).not.toContain("YES");
      expect(symbols).not.toContain("NO");

      // Agent balances and `collateral` param key off USDC, not COLLATERAL.
      const agents = api.agents as Array<{
        params: Record<string, unknown>;
        initial_balances: Record<string, number>;
      }>;
      for (const a of agents) {
        expect(a.params.collateral).toBe("USDC");
        expect(Object.keys(a.initial_balances)).toContain("USDC");
        expect(Object.keys(a.initial_balances)).not.toContain("COLLATERAL");
      }
    });
  });
});
