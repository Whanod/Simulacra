// Phase 4 regression guard: the results page bundle resolves from one
// `/runs/{id}/views/overview` fetch on initial load, and never falls back
// to the legacy `/runs/{id}/result` endpoint that the migration retired.
//
// Listing/sharing widgets that fire their own fetches (the run-picker
// fallback in the bundle hook, the wallet share-status badge) are scoped
// out of the assertion — they aren't part of the results-page bundle and
// don't gate the initial paint.

import { test, expect } from "@playwright/test";

const API_BASE = `http://127.0.0.1:${process.env.PLAYWRIGHT_API_PORT ?? "8100"}`;

test("results page paints from a single /views/overview fetch", async ({ page, request }) => {
  // Create a small run via the backend so we have a stable run_id.
  const runResp = await request.post(`${API_BASE}/simulations/run`, {
    data: {
      market: {
        type: "cfamm",
        tokens: [
          { id: "SOL", symbol: "SOL", decimals: 9, native: true, standard: "native" },
          { id: "USDC", symbol: "USDC", decimals: 6, standard: "spl" },
        ],
        fee_model: { type: "flat", params: { trade_fee_bps: 30 } },
        params: { initial_liquidity: 1_000_000, collateral_token: "USDC" },
      },
      agents: [
        {
          type: "noise",
          agent_id: "noise-1",
          params: { collateral: "USDC", frequency: 0.2 },
          initial_balances: { USDC: 1_000_000_000 },
        },
      ],
      num_rounds: 5,
      snapshot_interval: 1,
      seed: 4242,
    },
  });
  expect(runResp.ok()).toBeTruthy();
  const { run_id: runId } = await runResp.json();

  // Capture every backend request the page issues during initial paint.
  const apiRequests: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.startsWith(API_BASE)) {
      apiRequests.push(url.slice(API_BASE.length));
    }
  });

  // Visit the results page and wait for the overview view to land.
  await page.goto(`/results/${runId}`);
  // The run-id header is rendered inside `<Topbar>` once the bundle resolves.
  await page.waitForSelector("text=Results & Analytics", { state: "attached" });
  // Wait an extra tick to let any straggling fetches fire.
  await page.waitForTimeout(500);

  console.log("API calls during initial paint:", apiRequests);

  // The view endpoint must have been hit exactly once.
  const overviewHits = apiRequests.filter((u) =>
    u.startsWith(`/runs/${runId}/views/overview`),
  );
  expect(overviewHits).toHaveLength(1);

  // The legacy mega-result endpoint must NOT have been hit.
  const resultHits = apiRequests.filter(
    (u) => u === `/runs/${runId}/result` || u.startsWith(`/runs/${runId}/result?`),
  );
  expect(resultHits).toHaveLength(0);
});
