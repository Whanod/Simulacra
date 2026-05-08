#!/usr/bin/env bun
import { writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const OUT_DIR = path.resolve(__dirname, "..", "test", "fixtures", "api");
mkdirSync(OUT_DIR, { recursive: true });

async function get(p: string): Promise<unknown> {
  const r = await fetch(`${API}${p}`);
  if (!r.ok) throw new Error(`GET ${p} → ${r.status}`);
  return r.json();
}

async function post(p: string, body: unknown): Promise<unknown> {
  const r = await fetch(`${API}${p}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${p} → ${r.status}: ${await r.text()}`);
  return r.json();
}

function write(name: string, data: unknown) {
  const out = path.join(OUT_DIR, name);
  writeFileSync(out, JSON.stringify(data, null, 2));
  console.log(`wrote ${out}`);
}

const CFAMM_SPEC = {
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
      params: { collateral: "COLLATERAL", frequency: 0.0 },
      initial_balances: { COLLATERAL: 1_000_000_000 },
    },
  ],
  num_rounds: 5,
  snapshot_interval: 1,
  seed: 42,
};

async function main() {
  const runResponse = (await post("/simulations/run", CFAMM_SPEC)) as { run_id: string };
  const runId = runResponse.run_id;
  write("run_create.json", runResponse);

  write("runs_list.json", await get("/runs?limit=10"));
  write("run_get.json", await get(`/runs/${runId}`));
  write("run_result.json", await get(`/runs/${runId}/result`));
  write("run_events.json", await get(`/runs/${runId}/events?limit=100`));
  write("run_spec.json", await get(`/runs/${runId}/spec`));
  write("registry_list.json", await get("/registry"));
  write("registry_markets.json", await get("/registry/markets"));

  const sweepCreate = (await post("/sweeps/run", {
    spec: CFAMM_SPEC,
    param_grid: { num_rounds: [2, 3, 4], snapshot_interval: [1, 2] },
    seeds: [1, 2],
    metrics: {
      rounds: { type: "field", path: "num_rounds_executed" },
      doubled: { type: "field", path: "num_rounds" },
    },
  })) as { sweep_id: string };
  write("sweep_create.json", sweepCreate);
  const sweepId = sweepCreate.sweep_id;

  write("sweeps_list.json", await get("/sweeps?limit=10"));
  write("sweep_get.json", await get(`/sweeps/${sweepId}`));
  write("sweep_rows.json", await get(`/sweeps/${sweepId}/rows`));
  write(
    "sweep_recommendations.json",
    await post(`/sweeps/${sweepId}/recommendations`, {
      objective_metrics: ["rounds"],
      weights: { rounds: 1 },
      lower_is_better: { rounds: false },
      top_k: 3,
    }),
  );

  console.log("\nfixture capture complete");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
