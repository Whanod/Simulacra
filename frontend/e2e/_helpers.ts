import type { APIRequestContext } from "@playwright/test";

const API_PORT = Number(process.env.PLAYWRIGHT_API_PORT || 8100);
const FRONTEND_PORT = Number(process.env.PLAYWRIGHT_FRONTEND_PORT || 3100);
export const API_BASE = `http://127.0.0.1:${API_PORT}`;
export const FRONTEND_BASE = `http://127.0.0.1:${FRONTEND_PORT}`;

export const MINIMAL_SPEC = {
  market: {
    type: "cfamm",
    tokens: [
      { id: "YES", symbol: "YES", decimals: 9 },
      { id: "NO", symbol: "NO", decimals: 9 },
    ],
    params: { initial_liquidity: 1_000_000, collateral_token: "COLLATERAL" },
  },
  agents: [
    {
      type: "noise",
      agent_id: "noise-1",
      params: { collateral: "COLLATERAL", frequency: 0 },
      initial_balances: { COLLATERAL: 1_000_000_000 },
    },
  ],
  num_rounds: 3,
  snapshot_interval: 1,
  seed: 42,
};

export const WORLD_SPEC = {
  market: {
    type: "world",
    markets: {
      amm: {
        type: "cfamm",
        tokens: [
          { id: "YES", symbol: "YES", decimals: 9 },
          { id: "NO", symbol: "NO", decimals: 9 },
        ],
        params: { initial_liquidity: 1_000_000, collateral_token: "COLLATERAL" },
      },
      book: {
        type: "clob",
        pairs: [
          {
            base: { id: "YES", symbol: "YES", decimals: 9 },
            quote: { id: "COLLATERAL", symbol: "COL", decimals: 9 },
          },
        ],
      },
    },
  },
  agents: [
    {
      type: "arbitrageur",
      agent_id: "arb-1",
      params: { collateral: "COLLATERAL" },
      initial_balances: { COLLATERAL: 1_000_000_000 },
    },
  ],
  num_rounds: 6,
  snapshot_interval: 1,
  seed: 17,
};

export async function seedRun(
  request: APIRequestContext,
  overrides: { seed?: number; numRounds?: number } = {},
): Promise<string> {
  const spec = {
    ...MINIMAL_SPEC,
    seed: overrides.seed ?? 42,
    num_rounds: overrides.numRounds ?? MINIMAL_SPEC.num_rounds,
  };
  const res = await request.post(`${API_BASE}/simulations/run`, { data: spec });
  if (!res.ok()) throw new Error(`seedRun failed: ${res.status()} ${await res.text()}`);
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

export async function seedReplayRun(
  request: APIRequestContext,
  overrides: {
    slot?: number;
    counterfactuals?: Array<{ kind: string; params: Record<string, unknown> }>;
  } = {},
): Promise<string> {
  const slot = overrides.slot ?? 160_000_001;
  const res = await request.post(`${API_BASE}/v1/replay`, {
    data: {
      slot_range: [slot, slot],
      counterfactuals: overrides.counterfactuals ?? [],
    },
  });
  if (!res.ok()) {
    throw new Error(`seedReplayRun failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

export async function seedWorldRun(
  request: APIRequestContext,
  overrides: { seed?: number; numRounds?: number } = {},
): Promise<string> {
  const spec = {
    ...WORLD_SPEC,
    seed: overrides.seed ?? WORLD_SPEC.seed,
    num_rounds: overrides.numRounds ?? WORLD_SPEC.num_rounds,
  };
  const res = await request.post(`${API_BASE}/simulations/run`, { data: spec });
  if (!res.ok()) throw new Error(`seedWorldRun failed: ${res.status()} ${await res.text()}`);
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

export async function seedReport(
  request: APIRequestContext,
  runId: string,
  title = "Seeded report",
): Promise<string> {
  const body = {
    title,
    run_ids: [runId],
    sweep_ids: [],
    charts: [],
    exports: [],
    raw_artifacts: ["spec", "result", "events", "rounds"],
    sections: [],
  };
  const res = await request.post(`${API_BASE}/reports`, { data: body });
  if (!res.ok()) throw new Error(`seedReport failed: ${res.status()} ${await res.text()}`);
  const data = (await res.json()) as { report_id: string };
  return data.report_id;
}

export async function seedSweep(
  request: APIRequestContext,
): Promise<string> {
  const body = {
    spec: MINIMAL_SPEC,
    param_grid: {
      num_rounds: [2, 3, 4],
      snapshot_interval: [1, 2],
    },
    seeds: [1, 2],
    metrics: {
      rounds: { type: "field", path: "num_rounds_executed" },
    },
  };
  const res = await request.post(`${API_BASE}/sweeps/run`, { data: body });
  if (!res.ok()) throw new Error(`seedSweep failed: ${res.status()} ${await res.text()}`);
  const data = (await res.json()) as { sweep_id: string };
  return data.sweep_id;
}
