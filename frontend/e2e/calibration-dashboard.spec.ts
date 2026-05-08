import { test, expect } from "@playwright/test";

/**
 * PRD line 820 — `frontend/e2e/calibration-dashboard.spec.ts`.
 *
 * The /calibration dashboard (PRD line 787) renders the committed corpus
 * scoreboard from `solana-plans/calibration/corpus/<slot>/manifest.yaml` and
 * the per-metric threshold table from
 * `solana-plans/calibration/thresholds.yaml`. This spec asserts the page
 * loads without console errors and that the dashboard surfaces both the
 * per-slot scoreboard and the threshold table the PRD calls for.
 *
 * The spec is fully read-only: it does not seed runs or mutate the artifact
 * store, so it works against any environment where the calibration corpus
 * directory is committed to disk.
 */
test.describe("calibration dashboard", () => {
  test("dashboard_renders_corpus_status", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/calibration");

    await expect(page.getByTestId("calibration-dashboard")).toBeVisible();

    // Summary cards (PRD line 787 asks for per-corpus-slot coverage).
    await expect(page.getByTestId("calibration-summary-coverage")).toBeVisible();
    await expect(page.getByTestId("calibration-summary-regressions")).toBeVisible();
    await expect(page.getByTestId("calibration-summary-thresholds")).toBeVisible();

    // Threshold table from solana-plans/calibration/thresholds.yaml — the
    // PRD-required metric families must each have a row.
    const thresholdsTable = page.getByTestId("calibration-thresholds-table");
    await expect(thresholdsTable).toBeVisible();
    await expect(thresholdsTable.locator('[data-metric="pool_price"]')).toBeVisible();
    await expect(
      thresholdsTable.locator('[data-metric="liquidations_triggered"]'),
    ).toBeVisible();

    // Per-slot scoreboard. The corpus directory ships with multiple slot
    // dirs committed; the dashboard must surface at least one slot card.
    const slotCards = page.getByTestId("calibration-slot-card");
    await expect(slotCards.first()).toBeVisible();
    const slotCount = await slotCards.count();
    expect(
      slotCount,
      "calibration dashboard should render at least one corpus slot card",
    ).toBeGreaterThan(0);

    // Each card must expose its run-count + last-run row + trend block so
    // the PRD-line-787 deliverables (last-run timestamp, per-metric trend)
    // are reachable from the DOM. We only check the first card to keep
    // the spec resilient to corpus growth — every card is built from the
    // same component so one is sufficient as a smoke check.
    const firstCard = slotCards.first();
    await expect(firstCard.getByTestId("calibration-slot-run-count")).toBeVisible();
    await expect(firstCard.getByTestId("calibration-slot-last-run")).toBeVisible();
    await expect(firstCard.getByTestId("calibration-slot-trend")).toBeVisible();

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
