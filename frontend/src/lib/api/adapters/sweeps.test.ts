import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  configFromSpec,
  fromApiSweep,
  fromApiSweeps,
  recommendationsToResults,
  sweepConfigToApi,
  type ApiSweep,
  type ApiSweepRecommendations,
  type ApiSweepRowsResponse,
  type ApiSweepsListResponse,
} from "@/lib/api/adapters/sweeps";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "..", "test", "fixtures", "api");

function loadFixture<T>(name: string): T {
  return JSON.parse(readFileSync(path.join(FIXTURES, name), "utf8")) as T;
}

describe("sweeps adapter", () => {
  describe("configFromSpec", () => {
    it("derives params from the sweep's param_grid", () => {
      const sweep = loadFixture<ApiSweep>("sweep_get.json");
      const rows = loadFixture<ApiSweepRowsResponse>("sweep_rows.json").data;
      const config = configFromSpec(sweep, rows);

      expect(config.id).toBe(sweep.sweep_id);
      const numRounds = config.params.find((p) => p.parameter === "num_rounds")!;
      expect(numRounds.min).toBe(2);
      expect(numRounds.max).toBe(4);
      expect(numRounds.steps).toBe(3);

      const snapInterval = config.params.find((p) => p.parameter === "snapshot_interval")!;
      expect(snapInterval.min).toBe(1);
      expect(snapInterval.max).toBe(2);
      expect(snapInterval.steps).toBe(2);

      expect(config.totalRuns).toBe(rows.length);
      expect(config.seeds).toEqual([1, 2]);
      expect(["doubled", "rounds"]).toContain(config.targetMetric);
    });
  });

  describe("fromApiSweep", () => {
    it("merges sweep metadata + rows into a full SweepRun", () => {
      const sweep = loadFixture<ApiSweep>("sweep_get.json");
      const rows = loadFixture<ApiSweepRowsResponse>("sweep_rows.json").data;
      const run = fromApiSweep(sweep, rows);

      expect(run.id).toBe(sweep.sweep_id);
      expect(run.status).toBe("completed");
      expect(run.totalRuns).toBe(12);
      expect(run.completedRuns).toBe(12);
      expect(run.config.params.length).toBe(2);
      expect(run.results.length).toBeGreaterThan(0);
      expect(run.results[0].rank).toBe(1);

      // Heatmap is a 3×2 matrix (num_rounds ∈ {2,3,4} × snapshot_interval ∈ {1,2})
      expect(run.heatmap.length).toBe(3);
      expect(run.heatmap[0].length).toBe(2);
      expect(run.heatmapRowLabels).toEqual([2, 3, 4]);
      expect(run.heatmapColLabels).toEqual([1, 2]);
    });

    it("heatmap cells are means of target metric across seeds", () => {
      const sweep = loadFixture<ApiSweep>("sweep_get.json");
      const rows = loadFixture<ApiSweepRowsResponse>("sweep_rows.json").data;
      const run = fromApiSweep(sweep, rows);
      // In the fixture, targetMetric "doubled" just equals num_rounds, so
      // each row at num_rounds=2 yields doubled=2, averaged across seeds = 2.
      expect(run.heatmap[0][0]).toBe(2);
      expect(run.heatmap[1][0]).toBe(3);
      expect(run.heatmap[2][1]).toBe(4);
    });

    it("does not count rows with missing status as completed", () => {
      const run = fromApiSweep(
        {
          sweep_id: "sweep-1",
          status: "running",
          spec: { param_grid: {}, metrics: {} },
          summary: { row_count: 2 },
        },
        [
          { status: "completed" },
          {},
        ],
      );
      expect(run.completedRuns).toBe(1);
    });
  });

  describe("fromApiSweeps", () => {
    it("maps the list response", () => {
      const list = loadFixture<ApiSweepsListResponse>("sweeps_list.json");
      const sweeps = fromApiSweeps(list.sweeps);
      expect(sweeps.length).toBe(list.sweeps.length);
      expect(sweeps[0].id).toBe(list.sweeps[0].sweep_id);
    });
  });

  describe("sweepConfigToApi", () => {
    it("builds a SweepRunRequest payload from SweepConfig", () => {
      const req = sweepConfigToApi(
        { market: { type: "cfamm" } },
        {
          id: "local",
          params: [
            { parameter: "fee_rate", min: 0.001, max: 0.01, steps: 4 },
            { parameter: "noise", min: 0.1, max: 0.9, steps: 3 },
          ],
          seeds: [1, 2],
          parallelWorkers: 1,
          targetMetric: "kl_div",
          direction: "lower",
          validityGates: [],
          totalRuns: 24,
        },
      );
      expect(req.param_grid.fee_rate.length).toBe(4);
      expect(req.param_grid.fee_rate[0]).toBeCloseTo(0.001);
      expect(req.param_grid.fee_rate[3]).toBeCloseTo(0.01);
      expect(req.param_grid.noise).toEqual([0.1, 0.5, 0.9]);
      expect(req.seeds).toEqual([1, 2]);
      expect(req.metrics.kl_div).toBeDefined();
    });

    it("omits seeds when the config has no seeds", () => {
      const req = sweepConfigToApi(
        {},
        {
          id: "x",
          params: [{ parameter: "p", min: 0, max: 1, steps: 2 }],
          seeds: [],
          parallelWorkers: 1,
          targetMetric: "m",
          direction: "higher",
          validityGates: [],
          totalRuns: 2,
        },
      );
      expect(req.seeds).toBeUndefined();
    });
  });

  describe("recommendationsToResults", () => {
    it("parses top_configurations into SweepResult[]", () => {
      const recs = loadFixture<ApiSweepRecommendations>("sweep_recommendations.json");
      const out = recommendationsToResults(recs, ["num_rounds", "snapshot_interval"], ["rounds"]);
      expect(out.length).toBe(3);
      expect(out[0].rank).toBe(1);
      expect(out[0].params.num_rounds).toBe(4);
      expect(out[0].metrics.rounds).toBe(4);
      expect(out[0].score).toBe(1);
    });

    it("handles empty recommendations", () => {
      expect(recommendationsToResults({}, ["p"], ["m"])).toEqual([]);
    });
  });
});
