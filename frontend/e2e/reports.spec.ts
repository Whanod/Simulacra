import { test, expect } from "@playwright/test";
import { seedReport, seedRun } from "./_helpers";

test.describe("reports", () => {
  let runId = "";
  let reportId = "";

  test.beforeAll(async ({ request }) => {
    runId = await seedRun(request, { seed: 555 });
    reportId = await seedReport(request, runId, "E2E seeded report");
  });

  test("loads report, edits title, adds sections, publishes, downloads bundle", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto(`/reports/${reportId}`);

    // Title loaded from server.
    const titleInput = page.getByTestId("report-title");
    await expect(titleInput).toHaveValue("E2E seeded report");

    // Edit title → debounced auto-save should fire a PUT.
    const titleSave = page.waitForResponse(
      (res) =>
        res.url().includes(`/reports/${reportId}`) &&
        res.request().method() === "PUT",
    );
    await titleInput.fill("E2E edited report");
    await titleSave;

    // Add a chart section and a notes section — each mutation should auto-save.
    await page.getByTestId("add-section-chart").click();
    await page.getByTestId("add-section-notes").click();

    const chartSection = page.locator(
      '[data-testid="report-section"][data-section-type="chart"]',
    );
    const notesSection = page.locator(
      '[data-testid="report-section"][data-section-type="notes"]',
    );
    await expect(chartSection).toBeVisible();
    await expect(notesSection).toBeVisible();

    // Wait for the sections auto-save to round-trip.
    await page.waitForResponse(
      (res) =>
        res.url().includes(`/reports/${reportId}`) &&
        res.request().method() === "PUT",
    );

    // Publish the report.
    const publishSave = page.waitForResponse(
      (res) =>
        res.url().includes(`/reports/${reportId}`) &&
        res.request().method() === "PUT",
    );
    await page.getByTestId("report-publish").click();
    await publishSave;
    await expect(page.getByTestId("report-status")).toHaveText("published");

    // Reload and verify title + published status persisted server-side.
    // NOTE: download must come AFTER this — the backend rewrites report
    // status to "ready" when a bundle is built.
    await page.reload();
    await expect(page.getByTestId("report-title")).toHaveValue(
      "E2E edited report",
    );
    await expect(page.getByTestId("report-status")).toHaveText("published");

    // Download the bundle.
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("report-download").click();
    const dl = await downloadPromise;
    expect(dl.suggestedFilename()).toMatch(/\.zip$/);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
