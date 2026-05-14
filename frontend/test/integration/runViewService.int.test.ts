import { describe, it, expect } from "vitest";

import { simulationService } from "@/lib/services/simulationService";
import { runViewService } from "@/lib/services/runViewService";
import { metricsService } from "@/lib/services/metricsService";
import { eventsService } from "@/lib/services/eventsService";
import type { RunSpec } from "@/lib/types/simulations";

// A minimal CFAMM run is enough to exercise the page-shaped view bundle
// and the resource endpoints it complements. Three rounds keep the
// integration suite fast; the engine's deterministic enough that we can
// pin the seed and still assert on shape rather than golden values.
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
    seed: 4242,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

async function buildRun(): Promise<string> {
  const { runId } = await simulationService.buildSpec(DEFAULT_SPEC);
  expect(runId).toBeTruthy();
  return runId;
}

describe("runViewService (integration)", () => {
  it("fetchOverview returns the page-shaped bundle for a completed run", async () => {
    const runId = await buildRun();
    const view = await runViewService.fetchOverview(runId);

    expect(view.run.run_id).toBe(runId);
    expect(view.spec_summary.market_type).toBe("cfamm");
    expect(view.spec_summary.num_rounds).toBe(3);
    expect(view.spec_summary.seed).toBe(4242);
    expect(Array.isArray(view.spec_summary.agent_types)).toBe(true);

    // Tiles map is engine-derived; assert the contract, not membership of
    // any particular metric (which varies by spec and engine version).
    expect(view.tiles).toBeTypeOf("object");
    for (const value of Object.values(view.tiles)) {
      expect(typeof value).toBe("number");
    }

    expect(view.series.volume).toBeDefined();
    expect(view.series.num_actions).toBeDefined();
    expect(view.series.num_failed).toBeDefined();
    expect(view.series.gas_spent).toBeDefined();
    for (const point of view.series.volume) {
      expect(point.round).toBeGreaterThanOrEqual(0);
    }

    expect(Array.isArray(view.event_summary)).toBe(true);
    expect(view.event_summary.length).toBeGreaterThan(0);
    for (const row of view.event_summary) {
      expect(typeof row.type).toBe("string");
      expect(row.count).toBeGreaterThan(0);
    }
  });
});

describe("metricsService (integration)", () => {
  it("fetchSeries returns whole-market rollup with a round per row", async () => {
    const runId = await buildRun();
    const result = await metricsService.fetchSeries(runId, "volume");
    expect(result.runId).toBe(runId);
    expect(result.metric).toBe("volume");
    expect(result.agentId).toBeNull();
    expect(Array.isArray(result.series)).toBe(true);
    // Three rounds in the spec → three rollup rows (round 0 .. round 2).
    const rounds = result.series.map((row) => row.round);
    expect(rounds.length).toBeGreaterThan(0);
    expect([...rounds].sort((a, b) => a - b)).toEqual(rounds);
  });

  it("fetchSeries respects from/to bounds", async () => {
    const runId = await buildRun();
    const bounded = await metricsService.fetchSeries(runId, "num_actions", {
      fromRound: 1,
      toRound: 2,
    });
    expect(bounded.from).toBe(1);
    expect(bounded.to).toBe(2);
    for (const point of bounded.series) {
      expect(point.round).toBeGreaterThanOrEqual(1);
      expect(point.round).toBeLessThanOrEqual(2);
    }
  });
});

describe("eventsService (integration)", () => {
  it("fetchEvents returns adapted EvEntry rows and the raw payload", async () => {
    const runId = await buildRun();
    const page = await eventsService.fetchEvents(runId, { limit: 50 });
    expect(page.runId).toBe(runId);
    expect(page.events.length).toBeGreaterThan(0);
    expect(page.raw.length).toBe(page.events.length);
    // nextCursor only appears when the page fills exactly; for a tiny run
    // it will usually be null.
    if (page.nextCursor !== null) {
      expect(typeof page.nextCursor).toBe("number");
    }
  });

  it("fetchEvents narrows by event_type", async () => {
    const runId = await buildRun();
    const all = await eventsService.fetchEvents(runId, { limit: 500 });
    const distinctTypes = [...new Set(all.raw.map((e) => e.type ?? ""))];
    expect(distinctTypes.length).toBeGreaterThan(0);

    const target = distinctTypes[0];
    const narrowed = await eventsService.fetchEvents(runId, {
      eventType: target,
      limit: 500,
    });
    expect(narrowed.events.length).toBeGreaterThan(0);
    for (const raw of narrowed.raw) {
      expect(raw.type).toBe(target);
    }
  });
});
