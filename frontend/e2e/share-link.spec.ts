import { test, expect } from "@playwright/test";
import { API_BASE, FRONTEND_BASE, seedRun } from "./_helpers";

test("shareable_run_link_loads_results", async ({ request, browser }) => {
  const runId = await seedRun(request, { seed: 2709, numRounds: 4 });
  const sharedContext = await browser.newContext();
  const page = await sharedContext.newPage();
  const apiPaths: string[] = [];
  const consoleErrors: string[] = [];

  page.on("request", (req) => {
    if (!req.url().startsWith(API_BASE)) return;
    apiPaths.push(new URL(req.url()).pathname);
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  try {
    await page.goto(`${FRONTEND_BASE}/r/${encodeURIComponent(runId)}`);
    await expect(page).toHaveURL(new RegExp(`/results/${runId}\\?shared=1`));
    await expect(page.getByText("Composite Score")).toBeVisible();
    await expect(page.getByText("Heuristic blend of drawdown")).toBeVisible();

    expect(apiPaths).toContain(`/share/runs/${runId}`);
    expect(
      apiPaths.filter((path) => path === "/runs" || path.startsWith(`/runs/${runId}`)),
      `shared browser should not call private run endpoints: ${apiPaths.join(", ")}`,
    ).toEqual([]);
    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  } finally {
    await sharedContext.close();
  }
});
