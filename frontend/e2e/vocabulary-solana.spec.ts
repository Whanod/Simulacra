import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

const SOLANA_SPEC = {
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
      params: { collateral: "USDC", frequency: 0 },
      initial_balances: { USDC: 1_000_000_000 },
    },
  ],
  execution: {
    type: "solana_like",
    ordering: { type: "priority" },
    gas_model: { type: "compute_unit", params: {} },
  },
  num_rounds: 3,
  snapshot_interval: 1,
  seed: 4242,
};

async function seedSolanaRun(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${API_BASE}/simulations/run`, { data: SOLANA_SPEC });
  if (!res.ok()) {
    throw new Error(`seedSolanaRun failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

test.describe("vocabulary (solana)", () => {
  test("studio_renders_solana_vocab", async ({ page, request }) => {
    const runId = await seedSolanaRun(request);

    await page.goto(`/runner/${runId}`);
    // Sync-completed run auto-redirects to /results.
    await page.waitForURL(new RegExp(`/results/${runId}$`), {
      timeout: 15_000,
    });

    // Chain badge in the studio header reads "Solana".
    const badge = page.locator('[data-chain-badge="solana"]');
    await expect(badge).toBeVisible();
    await expect(badge).toHaveText("Solana");

    // Run summary line uses Solana idiom (e.g. "3 slots", not "3 rounds").
    const summary = page.locator("#content p").first();
    await expect(summary).toContainText(/\bslots\b/);
    await expect(summary).not.toContainText(/\brounds\b/i);

    // The chain-idiom-driven chrome should not surface neutral / Ethereum-era labels.
    const summaryText = (await summary.innerText()).toLowerCase();
    expect(summaryText).not.toContain("block time");
    expect(summaryText).not.toContain("block ");

    // The Solana chain idiom should not render the neutral fee label anywhere.
    await expect(page.locator("body")).not.toContainText("Fee model");
  });
});
