import { test, expect } from "@playwright/test";
import { seedRun } from "./_helpers";

test.describe("runner", () => {
  test("completed run auto-redirects to /results", async ({
    page,
    request,
  }) => {
    const runId = await seedRun(request, { seed: 777 });
    await page.goto(`/runner/${runId}`);
    await page.waitForURL(new RegExp(`/results/${runId}$`), {
      timeout: 15_000,
    });
  });

  test("unknown runId shows the lost-engine state with View Results fallback", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      // The lost-engine path deliberately hits GET /runs/{unknown} and catches
      // the 404 — browsers still log that response as a console error, so
      // filter it out here.
      const text = msg.text();
      if (text.includes("404")) return;
      consoleErrors.push(text);
    });

    await page.goto("/runner/nonexistent-run-id");
    await expect(
      page.getByText("Live engine no longer in memory"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "View Results" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Back to Dashboard" }),
    ).toBeVisible();

    // The "Back to Dashboard" button should route back.
    await page.getByRole("button", { name: "Back to Dashboard" }).click();
    await expect(page).toHaveURL(/\/dashboard$/);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
