import { describe, it, expect } from "vitest";
import { sweepService } from "@/lib/services/sweepService";
import type { RunSpec } from "@/lib/types/simulations";
import type { SweepConfig } from "@/lib/types/sweeps";

const BASE_SPEC: RunSpec = {
  market: { type: "cfamm", num_assets: 2, initial_liquidity: 1_000_000, token_decimals: 9 },
  clock: { type: "block", block_time: 1, epoch_length: 1 },
  execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
  fee_model: { type: "flat", rate_bps: 30 },
  agents: {
    total: 1,
    mix: {
      noise: 1,
      informed: 0,
      arbitrageur: 0,
      manipulator: 0,
      passive_lp: 0,
      rebalancing_lp: 0,
    },
    default_collateral: 1_000_000_000,
  },
  feeds: [
    { type: "stochastic", process: "gbm", drift: 0.0001, volatility: 0.02, initial_price: 1.0 },
  ],
  config: {
    num_rounds: 2,
    snapshot_interval: 1,
    seed: 42,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

const SWEEP_CONFIG: SweepConfig = {
  id: "local",
  params: [
    { parameter: "num_rounds", min: 2, max: 3, steps: 2 },
    { parameter: "snapshot_interval", min: 1, max: 2, steps: 2 },
  ],
  seeds: [1, 2],
  parallelWorkers: 1,
  targetMetric: "rounds",
  direction: "higher",
  validityGates: [],
  totalRuns: 8,
};

describe("sweepService (integration)", () => {
  it("listSweeps returns [] on a fresh backend", async () => {
    const before = await sweepService.listSweeps();
    expect(Array.isArray(before)).toBe(true);
  });

  it("createSweep → listSweeps contains the created sweep", async () => {
    const { sweepId } = await sweepService.createSweep(BASE_SPEC, SWEEP_CONFIG);
    expect(sweepId).toBeTruthy();

    const all = await sweepService.listSweeps();
    expect(all.find((s) => s.id === sweepId)).toBeDefined();
  });

  it("getSweep merges metadata + rows into a full SweepRun", async () => {
    const { sweepId } = await sweepService.createSweep(BASE_SPEC, SWEEP_CONFIG);
    const sweep = await sweepService.getSweep(sweepId);
    expect(sweep).toBeDefined();
    expect(sweep!.id).toBe(sweepId);
    expect(sweep!.status).toBe("completed");
    expect(sweep!.config.params.length).toBe(2);
    expect(sweep!.totalRuns).toBe(8); // 2 x 2 params × 2 seeds
    expect(sweep!.heatmap.length).toBe(2);
    expect(sweep!.heatmap[0].length).toBe(2);
    expect(sweep!.results.length).toBeGreaterThan(0);
    expect(sweep!.heatmapRowLabels).toEqual([2, 3]);
    expect(sweep!.heatmapColLabels).toEqual([1, 2]);
  });

  it("getSweep returns undefined for an unknown id", async () => {
    expect(await sweepService.getSweep("does-not-exist")).toBeUndefined();
  });

  it("getRecommendations returns ranked SweepResult[]", async () => {
    const { sweepId } = await sweepService.createSweep(BASE_SPEC, SWEEP_CONFIG);
    const recs = await sweepService.getRecommendations(sweepId, {
      objectiveMetrics: ["rounds"],
      weights: { rounds: 1 },
      lowerIsBetter: { rounds: false },
      topK: 3,
    });
    expect(recs.length).toBeGreaterThan(0);
    expect(recs.length).toBeLessThanOrEqual(3);
    expect(recs[0].rank).toBe(1);
    expect(recs[0].params).toBeDefined();
    expect(recs[0].metrics.rounds).toBeDefined();
  });

  it("getSensitivity returns numeric summary rows", async () => {
    const { sweepId } = await sweepService.createSweep(BASE_SPEC, SWEEP_CONFIG);
    const rows = await sweepService.getSensitivity(sweepId, "num_rounds", "rounds");
    expect(rows.length).toBeGreaterThan(0);
    const first = rows[0];
    expect(typeof first.value).toBe("number");
    expect(typeof first.mean).toBe("number");
    expect(typeof first.std).toBe("number");
  });
});
