import { test, expect } from "@playwright/test";
import { seedRun, seedWorldRun } from "./_helpers";

test.describe("results", () => {
  let runId = "";
  let worldRunId = "";

  test.beforeAll(async ({ request }) => {
    runId = await seedRun(request, { seed: 999 });
    worldRunId = await seedWorldRun(request, { seed: 1999 });
  });

  test("renders summary, metrics, charts, agents, and events tabs", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto(`/results/${runId}`);

    // Summary tab is the default.
    await expect(page.getByText("Composite Score")).toBeVisible();
    await expect(
      page.getByText("Heuristic blend of drawdown"),
    ).toBeVisible();

    // Metrics tab.
    await page.getByRole("button", { name: "Metrics", exact: true }).click();
    await expect(page.getByText("Computed Metrics")).toBeVisible();
    await expect(page.getByText("Max Drawdown")).toBeVisible();

    // Charts tab — at least one chart card should render (not the "No data"
    // fallback). We look for a Card title we know exists.
    await page.getByRole("button", { name: "Charts", exact: true }).click();
    await expect(page.getByText("Price Series")).toBeVisible();

    // Agents tab.
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await expect(page.getByText("Agent Final States")).toBeVisible();
    await page.locator("tbody tr").nth(0).click();
    await expect(page.getByText("Balance Over Time")).toBeVisible();
    await page.getByRole("button", { name: "Close agent story" }).click();

    // Events tab.
    await page.getByRole("button", { name: "Events", exact: true }).click();
    await expect(
      page.getByText(/Event Log|No events recorded/),
    ).toBeVisible();

    // Exports tab.
    await page.getByRole("button", { name: "Exports", exact: true }).click();
    await expect(page.getByText("Export Results")).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: /Download .csv/ }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("world runs expose the market selector and update the URL", async ({ page }) => {
    await page.goto(`/results/${worldRunId}`);
    const selector = page.getByTestId("results-market-select");
    await expect(selector).toBeVisible();
    await expect(selector.locator("option")).toContainText(["All Markets", "amm", "book"]);

    await selector.selectOption("amm");
    await expect(page).toHaveURL(new RegExp(`/results/${worldRunId}\\?market=amm`));
  });

  test("agent role filter options derive from observed roles (US-015)", async ({
    page,
  }) => {
    await page.goto(`/results/${runId}`);
    await page.getByRole("button", { name: "Agents", exact: true }).click();

    const filter = page.getByTestId("agent-role-filter");
    await expect(filter).toBeVisible();

    // The filter should include "All Roles" plus at least one observed
    // role. The exact list depends on the backend spec, but it must not
    // be the old hardcoded "noise/informed/arb/manip/lp/rebal" set.
    const optionTexts = await filter.locator("option").allInnerTexts();
    expect(optionTexts).toContain("All Roles");
    expect(optionTexts.length).toBeGreaterThanOrEqual(2);

    // Selecting an observed role narrows the visible rows.
    const observedRole = optionTexts.find((t) => t !== "All Roles");
    if (observedRole) {
      await filter.selectOption({ label: observedRole });
      // At least one row should remain (the seeded runs have agents of
      // every role that appears in the dropdown).
      await expect(page.locator("tbody tr").first()).toBeVisible();
    }
  });
});
