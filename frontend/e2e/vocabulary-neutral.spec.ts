import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

const NEUTRAL_SPEC = {
  market: {
    type: "cfamm",
    tokens: [
      { id: "YES", symbol: "YES", decimals: 18 },
      { id: "NO", symbol: "NO", decimals: 18 },
    ],
    fee_model: { type: "flat", params: { trade_fee_bps: 30 } },
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
  seed: 4242,
};

async function seedNeutralRun(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${API_BASE}/simulations/run`, { data: NEUTRAL_SPEC });
  if (!res.ok()) {
    throw new Error(`seedNeutralRun failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

test.describe("vocabulary (neutral)", () => {
  test("studio_renders_neutral_vocab_for_non_solana_spec", async ({ page, request }) => {
    const runId = await seedNeutralRun(request);

    await page.goto(`/runner/${runId}`);
    // Sync-completed run auto-redirects to /results.
    await page.waitForURL(new RegExp(`/results/${runId}$`), {
      timeout: 15_000,
    });

    // Chain badge should NOT be the Solana badge — it carries the neutral marker.
    const neutralBadge = page.locator('[data-chain-badge="neutral"]');
    await expect(neutralBadge).toBeVisible();
    await expect(page.locator('[data-chain-badge="solana"]')).toHaveCount(0);

    // Run summary line uses neutral idiom (e.g. "3 rounds", not "3 slots").
    const summary = page.locator("#content p").first();
    await expect(summary).toContainText(/\d+ rounds/i);
    await expect(summary).not.toContainText(/\bslots\b/i);

    // Neutral idiom should not surface Solana-only labels anywhere on the page.
    const body = page.locator("body");
    await expect(body).not.toContainText("Slot time");
    await expect(body).not.toContainText("Compute & priority fees");
    await expect(body).not.toContainText("Epoch (slots)");
  });
});
