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
  clock: {
    type: "solana_slot",
    params: { slot_duration_seconds: 0.4, epoch_length_slots: 432_000, skip_rate: 0.0 },
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

async function seedNeutralRun(request: APIRequestContext): Promise<string> {
  const res = await request.post(`${API_BASE}/simulations/run`, { data: NEUTRAL_SPEC });
  if (!res.ok()) {
    throw new Error(`seedNeutralRun failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

test.describe("theme-switch", () => {
  test("theme_attribute_follows_execution_type", async ({ page }) => {
    // Loads /builder, picks a Solana template (in-page state change), asserts
    // <html data-theme="solana">, then changes the exec-model select to a
    // non-Solana option and asserts the attribute flips to "neutral" without
    // a navigation.
    await page.goto("/builder");

    // Picker step renders before any template is applied — bExec defaults to
    // "direct", so data-theme is neutral here.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "neutral");
    const startUrl = page.url();

    // Click the Solana template card — applyTemplate() flips bExec to
    // "solana" and switches step to "form" via React state, no navigation.
    const solanaCard = page.locator(".grid-4 > .card").filter({
      has: page.getByRole("heading", { level: 3, name: "Whirlpool Fee Tuning", exact: true }),
    });
    await solanaCard.click();

    await expect(page.locator("html")).toHaveAttribute("data-theme", "solana");
    expect(page.url()).toBe(startUrl);

    // Switch the execution model to a non-Solana option in the same form —
    // RegistrySelect is a native <select>, so selectOption drives it.
    await page.locator("#exec-model").selectOption("direct");

    await expect(page.locator("html")).toHaveAttribute("data-theme", "neutral");
    expect(page.url()).toBe(startUrl);
  });

  test("live_chrome_replaces_placeholder_with_real_slot", async ({ page, request }) => {
    // Phase 1.1: a Solana run should populate the slot ticker with the
    // SolanaSlotClock's `current_slot` from the latest snapshot, replacing
    // the 0.2 placeholder once results load.
    const runId = await seedSolanaRun(request);

    await page.goto(`/runner/${runId}`);
    await page.waitForURL(new RegExp(`/results/${runId}$`), { timeout: 15_000 });

    const ticker = page.locator('#topbar [data-live-chrome]');
    await expect(ticker).toBeVisible();
    // Once round_snapshots load, the attribute flips to "live" and the
    // label reflects the most recent slot.
    await expect(ticker).toHaveAttribute("data-live-chrome", "live", { timeout: 10_000 });
    await expect(ticker).toContainText(/Slot \d+/);
  });

  test("primary_cta_uses_gradient_when_solana", async ({ page, request }) => {
    const runId = await seedSolanaRun(request);

    await page.goto(`/runner/${runId}`);
    await page.waitForURL(new RegExp(`/results/${runId}$`), { timeout: 15_000 });

    await expect(page.locator("html")).toHaveAttribute("data-theme", "solana");

    const cta = page.locator("#topbar .cta-primary").first();
    await expect(cta).toBeVisible();
    await expect(cta).toHaveCSS("background-image", /linear-gradient/);
  });

  test("primary_cta_no_gradient_when_neutral", async ({ page, request }) => {
    const runId = await seedNeutralRun(request);

    await page.goto(`/runner/${runId}`);
    await page.waitForURL(new RegExp(`/results/${runId}$`), { timeout: 15_000 });

    await expect(page.locator("html")).toHaveAttribute("data-theme", "neutral");

    const cta = page.locator("#topbar .cta-primary").first();
    await expect(cta).toBeVisible();
    await expect(cta).not.toHaveCSS("background-image", /linear-gradient/);
  });
});
