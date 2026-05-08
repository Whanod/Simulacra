import { describe, expect, it } from "vitest";
import { draftFromApiSpec, draftToApiSpec } from "./drafts";
import type { RegistryContractResponse } from "@/lib/types/contract";
import { SUPPORTED_CONTRACT_VERSION } from "@/lib/types/contract";

const CONTRACT: RegistryContractResponse = {
  contractVersion: SUPPORTED_CONTRACT_VERSION,
  categories: [
    {
      key: "reg-markets",
      label: "Markets",
      entities: [
        {
          category: "markets",
          type: "cfamm",
          label: "Constant Function AMM",
          builderSupported: true,
        },
      ],
    },
  ],
};

function baseSpec() {
  return {
    market: { type: "cfamm", num_assets: 2, fee_bps: 30 },
    clock: { type: "block", block_time: 12 },
    execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
    fee_model: { type: "flat", rate_bps: 30 },
    feeds: [
      { type: "stochastic", drift: 0.05, volatility: 0.2, initial_price: 1 },
    ],
    agents: {
      total: 10,
      role_params: {
        noise: { trade_min: 10, trade_max: 100 },
      },
    },
    config: {
      num_rounds: 100,
      seed: 42,
      information_filter: "full_transparency",
    },
  };
}

describe("draftFromApiSpec", () => {
  it("captures known entities with paths and raw snapshots", () => {
    const draft = draftFromApiSpec(baseSpec(), { contract: CONTRACT });

    const market = draft.entities.find((e) => e.configPath === "market");
    expect(market).toBeDefined();
    expect(market?.category).toBe("markets");
    expect(market?.type).toBe("cfamm");
    expect(market?.label).toBe("Constant Function AMM");
    expect(market?.raw).toEqual({ type: "cfamm", num_assets: 2, fee_bps: 30 });
    expect(market?.params).toEqual({ num_assets: 2, fee_bps: 30 });
  });

  it("records rawSpec as an untouched load-time snapshot", () => {
    const spec = baseSpec();
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    expect(draft.rawSpec).toEqual(spec);
    (spec.market as { fee_bps: number }).fee_bps = 999;
    expect((draft.rawSpec as { market: { fee_bps: number } }).market.fee_bps).toBe(30);
  });
});

describe("draftToApiSpec merge rule (US-005)", () => {
  it("overlays edited params key-by-key and preserves untouched raw keys", () => {
    const spec = baseSpec();
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    const market = draft.entities.find((e) => e.configPath === "market");
    if (!market) throw new Error("market entity missing");
    // Edit only fee_bps; num_assets must survive untouched.
    market.params = { fee_bps: 50 };
    const out = draftToApiSpec(draft) as {
      market: { type: string; num_assets: number; fee_bps: number };
    };
    expect(out.market.fee_bps).toBe(50);
    expect(out.market.num_assets).toBe(2);
    expect(out.market.type).toBe("cfamm");
  });
});

describe("draftFromApiSpec → draftToApiSpec round trip (US-006)", () => {
  it("round-trips an untouched known spec without loss", () => {
    const spec = baseSpec();
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    expect(draftToApiSpec(draft)).toEqual(spec);
  });

  it("preserves an unknown market type and its bespoke fields", () => {
    const spec = {
      ...baseSpec(),
      market: {
        type: "unknown_future_market",
        num_assets: 3,
        bespoke_knob: "value",
        nested: { a: 1, b: [2, 3] },
      },
    };
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    expect(draftToApiSpec(draft)).toEqual(spec);
  });

  it("preserves unknown top-level blocks the adapter does not claim", () => {
    const spec = {
      ...baseSpec(),
      custom_block: { whatever: 1, nested: { flag: true } },
      something_new: [1, 2, { x: "y" }],
    };
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    expect(draft.unknownBlocks.custom_block).toEqual({
      whatever: 1,
      nested: { flag: true },
    });
    expect(draftToApiSpec(draft)).toEqual(spec);
  });

  it("round-trips a mixed known/unknown spec with edits only on known fields", () => {
    const spec = {
      ...baseSpec(),
      market: {
        type: "cfamm",
        num_assets: 2,
        fee_bps: 30,
        undocumented_hint: "keep me",
      },
      execution: {
        model: "direct",
        ordering: "custom_backend_ordering",
        cost_model: "zero",
      },
      agents: {
        total: 10,
        role_params: {
          noise: { trade_min: 10, trade_max: 100, future_knob: { n: true } },
          future_role_xyz: { custom: 7 },
        },
      },
      vendor_extension: { hello: "world" },
    };
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });

    // Edit a known field; every unknown field must still survive.
    const market = draft.entities.find((e) => e.configPath === "market");
    if (!market) throw new Error("market entity missing");
    market.params = { ...market.params, fee_bps: 45 };

    const out = draftToApiSpec(draft) as {
      market: { fee_bps: number; undocumented_hint: string; num_assets: number };
      execution: { ordering: string };
      agents: {
        role_params: {
          noise: { trade_min: number; future_knob: { n: boolean } };
          future_role_xyz: { custom: number };
        };
      };
      vendor_extension: { hello: string };
    };

    expect(out.market.fee_bps).toBe(45);
    expect(out.market.undocumented_hint).toBe("keep me");
    expect(out.market.num_assets).toBe(2);
    expect(out.execution.ordering).toBe("custom_backend_ordering");
    expect(out.agents.role_params.noise.trade_min).toBe(10);
    expect(out.agents.role_params.noise.future_knob).toEqual({ n: true });
    expect(out.agents.role_params.future_role_xyz.custom).toBe(7);
    expect(out.vendor_extension.hello).toBe("world");
  });

  it("preserves unknown feed type and extra feed fields", () => {
    const spec = {
      ...baseSpec(),
      feeds: [
        {
          type: "exotic_feed",
          bespoke_a: 1,
          bespoke_b: { nested: [true, false] },
        },
      ],
    };
    const draft = draftFromApiSpec(spec, { contract: CONTRACT });
    expect(draftToApiSpec(draft)).toEqual(spec);
  });
});
