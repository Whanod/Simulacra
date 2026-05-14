import { describe, it, expect, vi, afterEach } from "vitest";

import { runViewService } from "@/lib/services/runViewService";
import { metricsService } from "@/lib/services/metricsService";
import { eventsService } from "@/lib/services/eventsService";
import { API_BASE_URL } from "@/lib/config";

// Unit-level tests against a stubbed `fetch` — exercise URL construction
// and response unwrapping without needing a live backend (the integration
// suite at `test/integration/runViewService.int.test.ts` covers the wire
// contract end-to-end). `API_BASE_URL` is read here (not hardcoded)
// because the integration suite's globalSetup boots uvicorn on a random
// port and exposes it via `NEXT_PUBLIC_API_URL` before tests collect.

const apiBase = API_BASE_URL;

type FetchMock = ReturnType<typeof vi.fn> & {
  __lastUrl?: string;
};

function installFetch(payload: unknown): FetchMock {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    (mock as FetchMock).__lastUrl =
      typeof input === "string" ? input : input.toString();
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as FetchMock;
  globalThis.fetch = mock as unknown as typeof fetch;
  return mock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("runViewService.fetchOverview", () => {
  it("hits /runs/{id}/views/overview and returns the bundle verbatim", async () => {
    const bundle = {
      run: { run_id: "run-1" },
      spec_summary: { market_type: "cfamm", agent_types: [], num_rounds: 3, seed: 1 },
      tiles: { slippage: 0.001 },
      series: {
        volume: [{ round: 0, value: 10 }],
        num_actions: [],
        num_failed: [],
        gas_spent: [],
      },
      event_summary: [{ type: "ACTION_EXECUTED", count: 5 }],
      // Phase 4.5 slices — empty/null payload still flows through
      // typed as-is so the page rewires can rely on every key.
      price_history: [{ SOL: 100 }],
      volume_history: null,
      liquidity_history: null,
      agent_final_states: { "agent-1": { realized_pnl: 0 } },
      whirlpool_snapshots: null,
      sandwich_summary: null,
      replay_diff: null,
      fee_history: [],
      // Phase 4 page-rewire slices.
      num_rounds_executed: 3,
      solana_slot_summary: null,
      bundle_outcomes_summary: null,
      jito_searcher_summary: null,
      replay_metrics: null,
    };
    const mock = installFetch(bundle);

    const view = await runViewService.fetchOverview("run-1");

    expect(mock.__lastUrl).toBe(`${apiBase}/runs/run-1/views/overview`);
    expect(view).toEqual(bundle);
  });

  it("parses the Phase 4 page-rewire slices when populated", async () => {
    const bundle = {
      run: { run_id: "run-2" },
      spec_summary: { market_type: "cfamm", agent_types: [], num_rounds: 2, seed: 1 },
      tiles: {},
      series: { volume: [], num_actions: [], num_failed: [], gas_spent: [] },
      event_summary: [],
      price_history: null,
      volume_history: null,
      liquidity_history: null,
      agent_final_states: null,
      whirlpool_snapshots: null,
      sandwich_summary: null,
      replay_diff: null,
      fee_history: [],
      num_rounds_executed: 2,
      solana_slot_summary: { current_slot: 999, current_leader: "alice" },
      bundle_outcomes_summary: {
        counts: { landed: 2, reverted: 1, dropped: 0 },
        timeline: { landed: [1, 1], reverted: [0, 1], dropped: [0, 0] },
        tips_paid_lamports: 1500,
        drop_reasons: {},
        landing_rate_stats: { avg: 0.75, stdev: 0.25, rounds_with_bundles: 2 },
      },
      jito_searcher_summary: {
        bundles_submitted: 5,
        bundles_landed: 2,
        tips_submitted_lamports: 1000,
        tips_paid_lamports: 400,
        realized_ev_lamports: 800,
        landing_rate: 0.4,
        tip_roi: 2,
        synthetic: false,
        calibration: null,
      },
      replay_metrics: { step: 7, matched: true },
    };
    installFetch(bundle);

    const view = await runViewService.fetchOverview("run-2");

    // Verify every Phase 4 field round-trips so a future wire change can't
    // silently strip one without the type/test catching it.
    expect(view.solana_slot_summary?.current_slot).toBe(999);
    expect(view.bundle_outcomes_summary?.counts.landed).toBe(2);
    expect(view.bundle_outcomes_summary?.landing_rate_stats.avg).toBe(0.75);
    expect(view.jito_searcher_summary?.realized_ev_lamports).toBe(800);
    expect(view.replay_metrics).toEqual({ step: 7, matched: true });
    expect(view.num_rounds_executed).toBe(2);
  });
});

describe("metricsService.fetchSeries", () => {
  it("forwards agent/from/to to the metric endpoint and reshapes the response", async () => {
    const mock = installFetch({
      run_id: "run-1",
      metric: "volume",
      agent_id: "victim-1",
      from: 10,
      to: 20,
      series: [{ round: 10, value: 1.5 }],
    });

    const result = await metricsService.fetchSeries("run-1", "volume", {
      agentId: "victim-1",
      fromRound: 10,
      toRound: 20,
    });

    expect(mock.__lastUrl).toContain("/runs/run-1/metrics/volume");
    expect(mock.__lastUrl).toContain("agent=victim-1");
    expect(mock.__lastUrl).toContain("from=10");
    expect(mock.__lastUrl).toContain("to=20");
    expect(result).toEqual({
      runId: "run-1",
      metric: "volume",
      agentId: "victim-1",
      from: 10,
      to: 20,
      series: [{ round: 10, value: 1.5 }],
    });
  });

  it("omits empty filters from the query string", async () => {
    const mock = installFetch({
      run_id: "run-1",
      metric: "gas_spent",
      agent_id: null,
      from: null,
      to: null,
      series: [],
    });
    await metricsService.fetchSeries("run-1", "gas_spent");
    expect(mock.__lastUrl).toBe(`${apiBase}/runs/run-1/metrics/gas_spent`);
  });
});

describe("eventsService", () => {
  it("fetchEvents adapts events and surfaces next_cursor when present", async () => {
    const raw = [
      {
        event_id: 1,
        run_id: "run-1",
        type: "ACTION_EXECUTED",
        round: 0,
        timestamp: 0,
        data: {},
      },
    ];
    installFetch({ run_id: "run-1", events: raw, next_cursor: 1 });

    const page = await eventsService.fetchEvents("run-1", {
      eventType: "ACTION_EXECUTED",
      limit: 1,
    });

    expect(page.runId).toBe("run-1");
    expect(page.raw).toEqual(raw);
    expect(page.events).toHaveLength(1);
    expect(page.events[0].evType).toBe("ACTION_EXECUTED");
    expect(page.nextCursor).toBe(1);
  });

  it("fetchEvents leaves nextCursor null when the backend omits it", async () => {
    installFetch({ run_id: "run-1", events: [] });
    const page = await eventsService.fetchEvents("run-1");
    expect(page.nextCursor).toBeNull();
  });

  it("fetchEvents preserves nextCursor=0 (a valid event_id, not absent)", async () => {
    installFetch({ run_id: "run-1", events: [], next_cursor: 0 });
    const page = await eventsService.fetchEvents("run-1");
    expect(page.nextCursor).toBe(0);
  });

  it("fetchEvents maps camelCase params to backend snake_case", async () => {
    const mock = installFetch({ run_id: "run-1", events: [] });
    await eventsService.fetchEvents("run-1", {
      eventType: "ACTION_EXECUTED",
      agentId: "victim-1",
      fromRound: 5,
      toRound: 10,
      cursor: 42,
      limit: 100,
      offset: 0,
    });
    const url = mock.__lastUrl ?? "";
    expect(url).toContain("event_type=ACTION_EXECUTED");
    expect(url).toContain("agent_id=victim-1");
    expect(url).toContain("from=5");
    expect(url).toContain("to=10");
    expect(url).toContain("cursor=42");
    expect(url).toContain("limit=100");
    // offset=0 is dropped by apiFetch's empty-value filter; that's
    // fine — the backend default is 0.
    expect(url).not.toMatch(/(?:[?&])round=/);
  });

  it("fetchCorrelation URL-encodes the correlation id", async () => {
    const mock = installFetch({
      run_id: "run-1",
      correlation_id: "abc/def",
      events: [],
    });
    await eventsService.fetchCorrelation("run-1", "abc/def");
    expect(mock.__lastUrl).toBe(
      `${apiBase}/runs/run-1/correlations/abc%2Fdef`,
    );
  });
});
