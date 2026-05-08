import { test, expect } from "@playwright/test";
import { seedReport, seedRun, seedSweep } from "./_helpers";

/**
 * Route-level smoke test: every page must load without throwing console
 * errors. This is deliberately lightweight — it does not interact with the
 * UI, just navigates and waits for the page to render. Per-page interaction
 * behavior lives in the route-specific spec files.
 *
 * One run, one sweep, and one report are seeded up front so routes that
 * need a runId/sweepId/reportId have real targets.
 */
test.describe("route smoke", () => {
  let runId = "";
  let sweepId = "";
  let reportId = "";

  test.beforeAll(async ({ request }) => {
    runId = await seedRun(request, { seed: 4242 });
    sweepId = await seedSweep(request);
    reportId = await seedReport(request, runId, "smoke-seed-report");
  });

  test("home redirects to dashboard", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    const res = await page.goto("/");
    expect(res!.status()).toBeLessThan(500);
    await expect(page).toHaveURL(/\/(dashboard)?$/);
    expect(errors).toEqual([]);
  });

  test("every route renders without console errors", async ({ page }) => {
    const routes = [
      "/dashboard",
      "/builder",
      "/compare",
      "/sweeps",
      `/sweeps/${sweepId}`,
      "/registry",
      `/results/${runId}`,
      `/reports/${reportId}`,
    ];

    for (const path of routes) {
      const errors = collectConsoleErrors(page);
      const res = await page.goto(path);
      expect(res, `navigation to ${path} returned null`).toBeTruthy();
      expect(res!.status(), `${path} returned 5xx`).toBeLessThan(500);
      // Give the page a beat to finish its initial data fetches.
      await page.waitForLoadState("networkidle").catch(() => undefined);
      expect(
        errors,
        `unexpected console errors on ${path}: ${errors.join(" | ")}`,
      ).toEqual([]);
    }
  });
});

function collectConsoleErrors(page: import("@playwright/test").Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    // 404s from the lost-engine path on routes like /runner/{unknown} are
    // logged as console errors even when the app catches them. Filter noise
    // the same way runner.spec.ts does.
    if (text.includes("404")) return;
    errors.push(text);
  });
  return errors;
}
