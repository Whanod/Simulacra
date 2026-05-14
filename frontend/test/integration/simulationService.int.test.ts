import { describe, it, expect } from "vitest";
import { simulationService } from "@/lib/services/simulationService";
import { runViewService } from "@/lib/services/runViewService";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { RunSpec } from "@/lib/types/simulations";

const DEFAULT_SPEC: RunSpec = {
  market: { type: "cfamm", num_assets: 2, initial_liquidity: 1_000_000, token_decimals: 9 },
  clock: { type: "block", block_time: 1, epoch_length: 1 },
  execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
  fee_model: { type: "flat", rate_bps: 30 },
  agents: {
    total: 2,
    mix: {
      noise: 1,
      informed: 0,
      arbitrageur: 0,
      manipulator: 0,
      passive_lp: 0,
      rebalancing_lp: 0,
    },
    default_collateral: 1_000_000,
  },
  feeds: [
    { type: "stochastic", process: "gbm", drift: 0.0001, volatility: 0.02, initial_price: 1.0 },
  ],
  config: {
    num_rounds: 3,
    snapshot_interval: 1,
    seed: 101,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

describe("simulationService (integration)", () => {
  describe("buildSpec + getRun (sync)", () => {
    it("builds a sync run and fetches it back", async () => {
      const { runId } = await simulationService.buildSpec(DEFAULT_SPEC);
      expect(runId).toBeTruthy();

      const run = await simulationService.getRun(runId);
      expect(run).toBeDefined();
      expect(run!.id).toBe(runId);
      expect(run!.status).toBe("completed");
      expect(run!.market).toBe("cfamm");
      expect(run!.seed).toBe(101);
      expect(run!.currentRound).toBe(3);
      expect(run!.totalRounds).toBe(3);
      expect(run!.agents).toBeGreaterThan(0);
      expect(run!.spec.config.num_rounds).toBe(3);
    });

    it("preserves configured agent counts and runtime settings", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        clock: { type: "block", block_time: 12, epoch_length: 4 },
        execution: { model: "direct", ordering: "priority", cost_model: "eip1559" },
        fee_model: { type: "dynamic", rate_bps: 45 },
        agents: {
          total: 10,
          mix: {
            noise: 0.6,
            informed: 0.4,
            arbitrageur: 0,
            manipulator: 0,
            passive_lp: 0,
            rebalancing_lp: 0,
          },
          default_collateral: 1_000_000,
        },
        feeds: [
          {
            type: "mean_revert",
            process: "mean_reversion",
            drift: 0.002,
            volatility: 0.1,
            initial_price: 1.1,
          },
        ],
        config: {
          ...DEFAULT_SPEC.config,
          seed: 102,
          information_filter: "delayed_information",
        },
      });

      const run = await simulationService.getRun(runId);
      const overview = await runViewService.fetchOverview(runId);
      const agentCount = Object.keys(overview.agent_final_states ?? {}).length;

      expect(run).toBeDefined();
      expect(run!.agents).toBe(10);
      expect(agentCount).toBe(10);
      expect(run!.exec).toBe("direct");
      expect(run!.ordering).toBe("priority");
      expect(run!.fee).toBe("dynamic 45bps");
      expect(run!.feed).toBe("mean_reversion");
      expect(run!.spec.clock.block_time).toBe(12);
      expect(run!.spec.execution.model).toBe("direct");
      expect(run!.spec.execution.cost_model).toBe("eip1559");
      expect(run!.spec.config.information_filter).toBe("delayed_information");
    });
  });

  describe("listRuns", () => {
    it("reflects newly-created runs", async () => {
      const before = await simulationService.listRuns();
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 1001 },
      });
      const after = await simulationService.listRuns();
      // Other test files may run in parallel against this backend, so we
      // can't assert an exact delta. The new run must appear and the count
      // must not have decreased.
      expect(after.length).toBeGreaterThanOrEqual(before.length + 1);
      expect(after.map((r) => r.id)).toContain(runId);
    });
  });

  describe("getEvents", () => {
    it("returns parsed EvEntry[]", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 4004 },
      });
      const events = await simulationService.getEvents(runId);
      expect(events.length).toBeGreaterThan(0);
      const first = events[0];
      expect(first.evType).toBeTruthy();
      expect(typeof first.round).toBe("number");
      expect(["trade", "lp", "fail", "oracle", "reward"]).toContain(first.cls);
    });

    it("supports limit/offset pagination against the backend", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 4005, num_rounds: 8 },
      });
      const firstPage = await simulationService.getEvents(runId, { limit: 2, offset: 0 });
      const secondPage = await simulationService.getEvents(runId, { limit: 2, offset: 2 });

      expect(firstPage).toHaveLength(2);
      expect(secondPage.length).toBeGreaterThan(0);
      expect(secondPage).not.toEqual(firstPage);
    });
  });

  describe("getSnapshots", () => {
    it("returns [] for a fresh run and rounds[] after a named snapshot", async () => {
      // Interactive build so we have a live engine to snapshot against.
      const result = await simulationService.buildSpec(
        { ...DEFAULT_SPEC, config: { ...DEFAULT_SPEC.config, seed: 7007 } },
        { mode: "interactive" },
      );
      expect("simulationId" in result).toBe(true);
      if (!("simulationId" in result)) return;

      const empty = await simulationService.getSnapshots(result.runId);
      expect(empty).toEqual([]);

      // Advance one step so there's a round to snapshot.
      await apiFetch(`/simulations/${result.simulationId}/step`, {
        method: "POST",
      });

      const snap = await simulationService.createSnapshot(
        result.simulationId,
        1,
        "checkpoint-a",
      );
      expect(snap.id).toBeTruthy();
      expect(snap.name).toBe("checkpoint-a");
      expect(snap.round).toBeGreaterThanOrEqual(0);

      const listed = await simulationService.getSnapshots(result.runId);
      expect(listed.length).toBe(1);
      expect(listed[0].id).toBe(snap.id);
    });
  });

  describe("buildSpec (interactive)", () => {
    it("returns {runId, simulationId}", async () => {
      const result = await simulationService.buildSpec(
        { ...DEFAULT_SPEC, config: { ...DEFAULT_SPEC.config, seed: 8008 } },
        { mode: "interactive" },
      );
      expect("simulationId" in result).toBe(true);
      if ("simulationId" in result) {
        expect(result.simulationId).toBeTruthy();
        expect(result.runId).toBeTruthy();
      }
    });
  });

  describe("compareRuns", () => {
    it("returns spec/metric/agent deltas for two runs", async () => {
      const a = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 21001 },
      });
      const b = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 21002 },
      });
      const view = await simulationService.compareRuns(a.runId, b.runId);
      expect(view.leftRunId).toBe(a.runId);
      expect(view.rightRunId).toBe(b.runId);
      // Different seeds → expect at least one spec_diff entry (the seed itself).
      expect(view.specDiff.length).toBeGreaterThan(0);
      // Metric diff always returns the canonical metric_keys.
      expect(view.metricDeltas.length).toBeGreaterThan(0);
      // Price summary deltas should be defined for at least one token.
      expect(view.priceSummary.length).toBeGreaterThan(0);
    });

    it("returns equal=true when comparing a run to itself", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 22002 },
      });
      const view = await simulationService.compareRuns(runId, runId);
      expect(view.equal).toBe(true);
      expect(view.specDiff).toEqual([]);
    });
  });

  describe("getSpec", () => {
    it("returns the run's original spec", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 11011 },
      });
      const spec = await simulationService.getSpec(runId);
      expect(spec).toBeDefined();
      expect((spec as { num_rounds?: number }).num_rounds).toBe(3);
    });
  });

  describe("getAgentTimeline", () => {
    it("returns timeline entries for a real agent", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 12012 },
      });
      const overview = await runViewService.fetchOverview(runId);
      const agentIds = Object.keys(overview.agent_final_states ?? {});
      expect(agentIds.length).toBeGreaterThan(0);
      const firstAgentId = agentIds[0];
      expect(firstAgentId).toBeTruthy();
      const timeline = await simulationService.getAgentTimeline(runId, firstAgentId);
      expect(Array.isArray(timeline)).toBe(true);
      // For a sync run with snapshot_interval=1 and num_rounds=3, we expect
      // at least one rounded entry (often num_rounds + 1 including round 0).
      expect(timeline.length).toBeGreaterThan(0);
      const entry = timeline[0];
      expect(typeof entry.round).toBe("number");
      expect(typeof entry.balance).toBe("number");
      expect(typeof entry.realizedPnl).toBe("number");
    });
  });

  describe("exportResult", () => {
    it("returns a non-empty CSV blob", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 13013 },
      });
      const blob = await simulationService.exportResult(runId, "csv");
      expect(blob.size).toBeGreaterThan(0);
      expect(blob.type).toContain("csv");
    });

    it("returns a non-empty JSON blob", async () => {
      const { runId } = await simulationService.buildSpec({
        ...DEFAULT_SPEC,
        config: { ...DEFAULT_SPEC.config, seed: 14014 },
      });
      const blob = await simulationService.exportResult(runId, "json");
      expect(blob.size).toBeGreaterThan(0);
      expect(blob.type).toContain("json");
    });
  });

  describe("getTemplates", () => {
    it("returns the backend's experiment templates mapped to SimTemplate[]", async () => {
      const templates = await simulationService.getTemplates();
      expect(templates.length).toBeGreaterThan(0);
      const ids = templates.map((t) => t.id);
      expect(ids).toContain("whirlpool-fee-tuning");
      const whirlpoolFee = templates.find((t) => t.id === "whirlpool-fee-tuning")!;
      expect(whirlpoolFee.name).toBeTruthy();
      expect(whirlpoolFee.description).toBeTruthy();
      expect(whirlpoolFee.spec.market?.type).toBe("cfamm");
      expect(whirlpoolFee.spec.config?.num_rounds).toBeGreaterThan(0);
      expect(whirlpoolFee.category).toBeTruthy();
    });
  });

  describe("validateSpec", () => {
    it("returns valid=true for a known-good spec", async () => {
      const res = await simulationService.validateSpec(DEFAULT_SPEC);
      expect(res.valid).toBe(true);
      expect(res.errors).toEqual([]);
    });

    it("returns valid=false with errors for a broken spec", async () => {
      const broken: RunSpec = {
        ...DEFAULT_SPEC,
        market: { ...DEFAULT_SPEC.market, type: "clob", token_decimals: -1 },
        config: { ...DEFAULT_SPEC.config, num_rounds: 0 },
      };
      const res = await simulationService.validateSpec(broken);
      expect(res.valid).toBe(false);
      expect(res.errors.length).toBeGreaterThan(0);
    });
  });

  describe("error paths", () => {
    it("getRun returns undefined for an unknown id (404 swallowed)", async () => {
      const run = await simulationService.getRun("does-not-exist-xyz");
      expect(run).toBeUndefined();
    });

    it("getEvents throws ApiError with status 404 for unknown run", async () => {
      await expect(simulationService.getEvents("does-not-exist-xyz")).rejects.toBeInstanceOf(
        ApiError,
      );
      try {
        await simulationService.getEvents("does-not-exist-xyz");
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(404);
      }
    });
  });
});
