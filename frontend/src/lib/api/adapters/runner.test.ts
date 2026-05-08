import { describe, it, expect } from "vitest";

import {
  fromApiAllMarkets,
  fromApiEngineEvents,
  fromApiParameters,
  fromApiStatus,
  fromApiStep,
  fromApiViolations,
  type ApiStepResponse,
} from "@/lib/api/adapters/runner";

const CFAMM_STEP: ApiStepResponse = {
  simulation_id: "sim-1",
  run_id: "run-1",
  round: 3,
  is_complete: false,
  snapshot: {
    round: 3,
    timestamp: 30,
    epoch: 0,
    market_state: {
      num_assets: 2,
      tokens: ["YES", "NO"],
      reserves: { YES: 1_010_000, NO: 990_000 },
      prices: { YES: 1.02, NO: 0.98 },
      total_liquidity: 2_000_000,
      __type__: "AmmSnapshot",
    },
    agent_states: {
      "noise-1": {
        agent_id: "noise-1",
        balances: { YES: 100, NO: 50, COLLATERAL: 5_000 },
        cumulative_volume: 1234,
        realized_pnl: 12,
      },
      "noise-2": {
        agent_id: "noise-2",
        balances: { YES: 0, NO: 0, COLLATERAL: 6_000 },
        cumulative_volume: 5_678,
        realized_pnl: -3,
      },
    },
  },
};

describe("runner adapter", () => {
  describe("fromApiStep", () => {
    it("extracts tokens, prices, reserves, and totals from a CFAMM step", () => {
      const delta = fromApiStep(CFAMM_STEP);
      expect(delta.round).toBe(3);
      expect(delta.tokens).toEqual(["YES", "NO"]);
      expect(delta.prices).toEqual([1.02, 0.98]);
      expect(delta.reserves).toEqual([1_010_000, 990_000]);
      expect(delta.totalLiquidity).toBe(2_000_000);
      expect(delta.totalCumulativeVolume).toBe(1234 + 5678);
      expect(delta.volumeDelta).toBe(0); // no prior baseline
      expect(delta.isComplete).toBe(false);
    });

    it("derives a positive volume delta when prior baseline is given", () => {
      const delta = fromApiStep(CFAMM_STEP, { totalCumulativeVolume: 1000 });
      expect(delta.volumeDelta).toBe(1234 + 5678 - 1000);
    });

    it("aggregates per-agent balance totals", () => {
      const delta = fromApiStep(CFAMM_STEP);
      expect(delta.agentBalances["noise-1"]).toBe(100 + 50 + 5000);
      expect(delta.agentBalances["noise-2"]).toBe(6000);
    });

    it("falls back to all_market_states for world specs", () => {
      const worldStep: ApiStepResponse = {
        simulation_id: "sim-w",
        run_id: "run-w",
        round: 1,
        is_complete: false,
        snapshot: {
          round: 1,
          all_market_states: {
            amm: {
              tokens: ["A", "B"],
              prices: { A: 2, B: 0.5 },
              reserves: { A: 100, B: 200 },
              total_liquidity: 300,
            },
            book: {
              tokens: ["A", "B"],
              best_bid: { A: 1.95, B: 0.49 },
              best_ask: { A: 2.05, B: 0.51 },
            },
          },
          agent_states: {},
        },
      };
      const delta = fromApiStep(worldStep);
      expect(delta.tokens).toEqual(["A", "B"]);
      expect(delta.prices).toEqual([2, 0.5]);
      expect(delta.reserves).toEqual([100, 200]);
    });

    it("selects the requested market from a world snapshot", () => {
      const worldStep: ApiStepResponse = {
        simulation_id: "sim-w",
        run_id: "run-w",
        round: 1,
        is_complete: false,
        snapshot: {
          round: 1,
          all_market_states: {
            amm: {
              tokens: ["A", "B"],
              prices: { A: 2, B: 0.5 },
              reserves: { A: 100, B: 200 },
            },
            book: {
              tokens: ["A", "B"],
              best_bid: { A: 1.95, B: 0.49 },
              best_ask: { A: 2.05, B: 0.51 },
            },
          },
          agent_states: {},
        },
      };
      const delta = fromApiStep(worldStep, undefined, "book");
      expect(delta.tokens).toEqual(["A", "B"]);
      expect(delta.prices).toEqual([2, 0.5]);
      expect(delta.reserves).toEqual([0, 0]);
    });

    it("handles missing snapshot fields gracefully", () => {
      const delta = fromApiStep({
        simulation_id: "sim",
        run_id: null,
        round: 0,
        is_complete: true,
        snapshot: {},
      });
      expect(delta.tokens).toEqual([]);
      expect(delta.prices).toEqual([]);
      expect(delta.reserves).toEqual([]);
      expect(delta.totalLiquidity).toBe(0);
      expect(delta.isComplete).toBe(true);
    });
  });

  describe("fromApiStatus", () => {
    it("normalizes the status response", () => {
      const status = fromApiStatus({
        simulation_id: "sim-1",
        run_id: "run-1",
        current_round: 12,
        is_complete: false,
        cancelled: false,
      });
      expect(status.simulationId).toBe("sim-1");
      expect(status.runId).toBe("run-1");
      expect(status.currentRound).toBe(12);
      expect(status.isComplete).toBe(false);
      expect(status.cancelled).toBe(false);
    });
  });

  describe("fromApiAllMarkets", () => {
    it("flattens the states map into a sorted list", () => {
      const list = fromApiAllMarkets({
        simulation_id: "sim-w",
        states: {
          amm: { tokens: ["A", "B"] },
          book: { tokens: ["A", "B"] },
        },
      });
      expect(list).toHaveLength(2);
      expect(list.map((m) => m.name).sort()).toEqual(["amm", "book"]);
    });
  });

  describe("fromApiParameters", () => {
    it("merges params with pending changes", () => {
      const view = fromApiParameters({
        params: { fee_rate: 0.003, gas_price: 0 },
        pending: [{ key: "fee_rate", value: 0.005, execute_at_round: 100 }],
        history: [],
      });
      const fee = view.rows.find((r) => r.key === "fee_rate");
      expect(fee?.value).toBe(0.003);
      expect(fee?.pendingAtRound).toBe(100);
      expect(view.rows).toHaveLength(2);
    });

    it("creates a row for a pending key with no current value", () => {
      const view = fromApiParameters({
        params: {},
        pending: [{ key: "spread", value: 0.01, execute_at_round: 50 }],
        history: [],
      });
      expect(view.rows[0].key).toBe("spread");
      expect(view.rows[0].value).toBeUndefined();
      expect(view.rows[0].pendingAtRound).toBe(50);
    });
  });

  describe("fromApiViolations", () => {
    it("maps violations", () => {
      const rows = fromApiViolations({
        violations: [
          { round: 5, message: "negative reserve" },
          { round: 7, message: "agent insolvent" },
        ],
      });
      expect(rows).toHaveLength(2);
      expect(rows[0].round).toBe(5);
      expect(rows[1].message).toBe("agent insolvent");
    });
  });

  describe("fromApiEngineEvents", () => {
    it("delegates to fromApiEvent", () => {
      const events = fromApiEngineEvents({
        events: [
          { type: "ACTION_EXECUTED", round: 1, data: { agent_id: "noise-1" } },
          { type: "ACTION_FAILED", round: 2, data: { agent_id: "noise-2", reason: "oops" } },
        ],
      });
      expect(events).toHaveLength(2);
      // US-015: class is hash-derived; we only check the bucket is legal.
      const allowed = new Set(["trade", "lp", "oracle", "reward", "fail"]);
      expect(allowed.has(events[0].cls)).toBe(true);
      expect(allowed.has(events[1].cls)).toBe(true);
      // And that detail wiring still reaches describeEvent.
      expect(events[0].detail).toContain("noise-1");
      expect(events[1].detail).toContain("noise-2");
    });
  });
});
