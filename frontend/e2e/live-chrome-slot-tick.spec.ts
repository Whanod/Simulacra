import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

function solanaSpec(numRounds: number) {
  return {
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
    num_rounds: numRounds,
    snapshot_interval: 1,
    seed: 4242,
  };
}

async function seedSolanaRun(
  request: APIRequestContext,
  numRounds: number,
): Promise<string> {
  const res = await request.post(`${API_BASE}/simulations/run`, {
    data: solanaSpec(numRounds),
  });
  if (!res.ok()) {
    throw new Error(`seedSolanaRun failed: ${res.status()} ${await res.text()}`);
  }
  const body = (await res.json()) as { run_id: string };
  return body.run_id;
}

function readSlot(label: string | null): number {
  const match = (label ?? "").match(/Slot (\d+)/);
  if (!match) throw new Error(`slot ticker label did not match /Slot \\d+/: ${label}`);
  return parseInt(match[1], 10);
}

test.describe("live-chrome-slot-tick", () => {
  test("header_slot_tick_advances", async ({ page, request }) => {
    // Phase 1.1 (US-001): the header slot ticker reflects the
    // SolanaSlotClock's `current_slot`, and that slot increments as
    // additional rounds tick. We compare two Solana runs that share the
    // same spec except for `num_rounds`: the longer run must surface a
    // strictly larger slot in the header. This proves the tick advances
    // over the configured-rounds interval — not just that *some* slot
    // shows up.
    const SHORT_ROUNDS = 5;
    const LONG_ROUNDS = 15;

    const shortRunId = await seedSolanaRun(request, SHORT_ROUNDS);
    await page.goto(`/runner/${shortRunId}`);
    await page.waitForURL(new RegExp(`/results/${shortRunId}$`), { timeout: 15_000 });

    const tickerShort = page.locator('#topbar [data-live-chrome]');
    await expect(tickerShort).toHaveAttribute("data-live-chrome", "live", {
      timeout: 10_000,
    });
    await expect(tickerShort).toContainText(/Slot \d+/);
    const shortSlot = readSlot(await tickerShort.textContent());
    // SolanaSlotClock ticks once per round, so the final slot should be
    // at least num_rounds. Allow >= rather than == in case the engine
    // ever evolves to skip slots in the default (skip_rate=0) path.
    expect(shortSlot).toBeGreaterThanOrEqual(SHORT_ROUNDS);

    const longRunId = await seedSolanaRun(request, LONG_ROUNDS);
    await page.goto(`/runner/${longRunId}`);
    await page.waitForURL(new RegExp(`/results/${longRunId}$`), { timeout: 15_000 });

    const tickerLong = page.locator('#topbar [data-live-chrome]');
    await expect(tickerLong).toHaveAttribute("data-live-chrome", "live", {
      timeout: 10_000,
    });
    await expect(tickerLong).toContainText(/Slot \d+/);
    const longSlot = readSlot(await tickerLong.textContent());
    expect(longSlot).toBeGreaterThanOrEqual(LONG_ROUNDS);

    // The advance: more configured rounds → strictly larger surfaced slot.
    expect(longSlot).toBeGreaterThan(shortSlot);
  });
});
