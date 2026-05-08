import { describe, it, expect } from "vitest";

import {
  fromApiCompare,
  type ApiCompareResponse,
} from "@/lib/api/adapters/compare";

const SAMPLE: ApiCompareResponse = {
  left_run_id: "run-A",
  right_run_id: "run-B",
  equal: false,
  spec_diff: {
    "agents.0.params.frequency": { left: 0.1, right: 0.5 },
    "seed": { left: 42, right: 99 },
  },
  metric_diff: {
    num_rounds: { left: 10, right: 10, delta: 0 },
    num_rounds_executed: { left: 10, right: 8, delta: -2 },
    seed: { left: 42, right: 99, delta: 57 },
    stopped_early: { left: false, right: true, delta: null },
    cancelled: { left: false, right: false, delta: null },
  },
  price_summary_delta: {
    YES: {
      left: { start: 1, end: 1.05, delta: 0.05 },
      right: { start: 1, end: 1.12, delta: 0.12 },
      delta_end: 0.07,
    },
    NO: {
      left: { start: 1, end: 0.95, delta: -0.05 },
      right: { start: 1, end: 0.88, delta: -0.12 },
      delta_end: -0.07,
    },
  },
  agent_summary_delta: {
    "noise-1": {
      left: { realized_pnl: 100 },
      right: { realized_pnl: 250 },
      delta_realized_pnl: 150,
    },
    "lp-1": {
      left: { realized_pnl: 500 },
      right: { realized_pnl: -200 },
      delta_realized_pnl: -700,
    },
  },
};

describe("compare adapter", () => {
  it("flattens spec_diff into rows", () => {
    const view = fromApiCompare(SAMPLE);
    expect(view.specDiff).toHaveLength(2);
    const seedRow = view.specDiff.find((r) => r.key === "seed");
    expect(seedRow?.left).toBe(42);
    expect(seedRow?.right).toBe(99);
  });

  it("classifies metric direction by metric semantics", () => {
    const view = fromApiCompare(SAMPLE);
    const executed = view.metricDeltas.find((m) => m.metric === "num_rounds_executed")!;
    expect(executed.delta).toBe(-2);
    // num_rounds_executed: higher is better → -2 is worse.
    expect(executed.direction).toBe("worse");

    const stoppedEarly = view.metricDeltas.find((m) => m.metric === "stopped_early")!;
    // stopped_early: lower is better; left=false (0), right=true (1) → delta=1 → worse.
    expect(stoppedEarly.delta).toBe(1);
    expect(stoppedEarly.direction).toBe("worse");

    const cancelled = view.metricDeltas.find((m) => m.metric === "cancelled")!;
    // both false → delta=0 → neutral.
    expect(cancelled.delta).toBe(0);
    expect(cancelled.direction).toBe("neutral");
  });

  it("maps price_summary_delta to rows", () => {
    const view = fromApiCompare(SAMPLE);
    expect(view.priceSummary).toHaveLength(2);
    const yes = view.priceSummary.find((r) => r.key === "YES")!;
    expect(yes.leftEnd).toBe(1.05);
    expect(yes.rightEnd).toBe(1.12);
    expect(yes.deltaEnd).toBe(0.07);
  });

  it("sorts agent_summary_delta by absolute delta", () => {
    const view = fromApiCompare(SAMPLE);
    expect(view.agentSummary[0].agentId).toBe("lp-1"); // |−700| > |+150|
    expect(view.agentSummary[1].agentId).toBe("noise-1");
  });

  it("survives an empty diff payload", () => {
    const view = fromApiCompare({
      left_run_id: "x",
      right_run_id: "y",
      equal: true,
      spec_diff: {},
      metric_diff: {},
      price_summary_delta: {},
      agent_summary_delta: {},
    });
    expect(view.equal).toBe(true);
    expect(view.specDiff).toEqual([]);
    expect(view.metricDeltas).toEqual([]);
    expect(view.priceSummary).toEqual([]);
    expect(view.agentSummary).toEqual([]);
  });
});
