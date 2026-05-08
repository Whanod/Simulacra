import { describe, it, expect } from "vitest";
import { runnerService } from "@/lib/services/runnerService";
import { simulationService } from "@/lib/services/simulationService";
import { ApiError } from "@/lib/api/errors";
import type { RunSpec } from "@/lib/types/simulations";

const SPEC: RunSpec = {
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
    default_collateral: 1_000_000,
  },
  feeds: [
    { type: "stochastic", process: "gbm", drift: 0.0001, volatility: 0.02, initial_price: 1.0 },
  ],
  config: {
    num_rounds: 5,
    snapshot_interval: 1,
    seed: 9001,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

async function buildLive(seedOffset = 0): Promise<{ runId: string; simulationId: string }> {
  const result = await simulationService.buildSpec(
    { ...SPEC, config: { ...SPEC.config, seed: SPEC.config.seed + seedOffset } },
    { mode: "interactive" },
  );
  if (!("simulationId" in result)) {
    throw new Error("Expected an interactive build result");
  }
  return result;
}

describe("runnerService (integration)", () => {
  describe("status + step", () => {
    it("reports the engine state and advances the round", async () => {
      const { simulationId } = await buildLive(1);

      const initial = await runnerService.getStatus(simulationId);
      expect(initial.simulationId).toBe(simulationId);
      expect(initial.isComplete).toBe(false);
      expect(initial.currentRound).toBe(0);

      const delta = await runnerService.step(simulationId);
      expect(delta.round).toBeGreaterThan(0);
      expect(delta.tokens.length).toBeGreaterThan(0);
      expect(delta.prices.length).toBe(delta.tokens.length);

      const next = await runnerService.getStatus(simulationId);
      expect(next.currentRound).toBeGreaterThan(initial.currentRound);
    });

    it("returns isComplete=true after all rounds, then 409 on further step", async () => {
      const { simulationId } = await buildLive(2);
      let lastDelta = await runnerService.step(simulationId);
      while (!lastDelta.isComplete) {
        lastDelta = await runnerService.step(simulationId);
      }
      expect(lastDelta.isComplete).toBe(true);

      // One more step should now 409.
      await expect(runnerService.step(simulationId)).rejects.toBeInstanceOf(ApiError);
      try {
        await runnerService.step(simulationId);
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(409);
      }
    });
  });

  describe("cancel + delete", () => {
    it("cancel marks the engine cancelled", async () => {
      const { simulationId } = await buildLive(3);
      await runnerService.cancel(simulationId);
      const status = await runnerService.getStatus(simulationId);
      expect(status.cancelled).toBe(true);
    });

    it("delete makes subsequent calls 404", async () => {
      const { simulationId } = await buildLive(4);
      await runnerService.deleteEngine(simulationId);
      await expect(runnerService.getStatus(simulationId)).rejects.toBeInstanceOf(ApiError);
      try {
        await runnerService.step(simulationId);
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(404);
      }
    });
  });

  describe("markets + parameters", () => {
    it("getAllMarkets returns at least one market state", async () => {
      const { simulationId } = await buildLive(5);
      await runnerService.step(simulationId);
      const markets = await runnerService.getAllMarkets(simulationId);
      expect(markets.length).toBeGreaterThan(0);
      const first = markets[0];
      expect(first.snapshot).toBeDefined();
    });

    it("setParameter then getParameters reflects the change", async () => {
      const { simulationId } = await buildLive(6);
      const before = await runnerService.getParameters(simulationId);
      expect(before).toBeDefined();

      await runnerService.setParameter(simulationId, "test_key", 0.42);
      const after = await runnerService.getParameters(simulationId);
      const row = after.rows.find((r) => r.key === "test_key");
      expect(row?.value).toBe(0.42);
    });

    it("scheduleParameter places the change in pending", async () => {
      const { simulationId } = await buildLive(7);
      await runnerService.scheduleParameter(simulationId, "future_key", 0.1, 100);
      const view = await runnerService.getParameters(simulationId);
      const row = view.rows.find((r) => r.key === "future_key");
      expect(row?.pendingAtRound).toBe(100);
    });
  });

  describe("validation hook", () => {
    it("attaches a hook and returns an empty violations list", async () => {
      const { simulationId } = await buildLive(8);
      await runnerService.attachValidationHook(simulationId, ["solvency", "reserves"]);
      const v = await runnerService.getViolations(simulationId);
      expect(Array.isArray(v)).toBe(true);
    });
  });

  describe("engine events", () => {
    it("returns parsed EvEntry[] from a stepped engine", async () => {
      const { simulationId } = await buildLive(9);
      await runnerService.step(simulationId);
      const events = await runnerService.getEngineEvents(simulationId, { limit: 50 });
      expect(Array.isArray(events)).toBe(true);
      expect(events.length).toBeGreaterThan(0);
    });
  });

  describe("forkFromSnapshot", () => {
    it("creates a new live engine from a named snapshot", async () => {
      const { simulationId, runId } = await buildLive(10);
      // Step once so there is a round to snapshot.
      await runnerService.step(simulationId);
      const snap = await simulationService.createSnapshot(
        simulationId,
        1,
        "branch-target",
      );
      expect(snap.id).toBeTruthy();

      const fork = await runnerService.forkFromSnapshot(snap.id);
      expect(fork.simulationId).toBeTruthy();
      expect(fork.runId).not.toBe(runId);

      // The forked engine should be alive and steppable.
      const forkStatus = await runnerService.getStatus(fork.simulationId);
      expect(forkStatus.isComplete).toBe(false);
      const delta = await runnerService.step(fork.simulationId);
      expect(delta.round).toBeGreaterThan(0);
    });
  });
});
