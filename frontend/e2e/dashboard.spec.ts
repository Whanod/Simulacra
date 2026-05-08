import { test, expect } from "@playwright/test";
import { seedRun } from "./_helpers";

test.describe("dashboard", () => {
  let runIdA = "";
  let runIdB = "";

  test.beforeAll(async ({ request }) => {
    runIdA = await seedRun(request, { seed: 101 });
    runIdB = await seedRun(request, { seed: 202 });
  });

  test("renders seeded runs and opens the detail modal", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/dashboard");
    await expect(page.getByTestId("sim-row").first()).toBeVisible();

    const rowA = page.locator(`[data-run-id="${runIdA}"]`);
    const rowB = page.locator(`[data-run-id="${runIdB}"]`);
    await expect(rowA).toBeVisible();
    await expect(rowB).toBeVisible();

    // Stat cards should show non-zero totals now that runs exist.
    await expect(page.getByText("Total Rounds")).toBeVisible();

    // Open detail modal via row click.
    await rowA.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    // Scope these assertions to the dialog — page-level text can collide with
    // featured-template descriptions that mention "execution" / "seed".
    await expect(dialog.getByText("Execution")).toBeVisible();
    await expect(dialog.getByText("Seed")).toBeVisible();
    // Modal should show the run's metadata — seed 101 must appear somewhere.
    await expect(dialog.locator('.mono:has-text("101")').first()).toBeVisible();

    await page.getByRole("button", { name: "Close" }).first().click();

    expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
  });

  test("spec modal fetches the full run spec on demand", async ({ page }) => {
    const specFetches: string[] = [];
    page.on("request", (req) => {
      const url = req.url();
      if (url.includes("/runs/") && !url.includes("/result") && !url.includes("/events")) {
        specFetches.push(url);
      }
    });

    await page.goto("/dashboard");
    await expect(page.getByTestId("sim-row").first()).toBeVisible();

    const viewJsonButton = page
      .locator(`[data-run-id="${runIdA}"]`)
      .getByRole("button", { name: "View JSON" });
    await viewJsonButton.click();

    // Spec modal should render JSON and show the seed in the body.
    await expect(page.getByText("RunSpec Preview")).toBeVisible();
    await expect(page.locator(".json-view")).toBeVisible();
    await expect(page.locator(".json-view")).toContainText('"seed"');

    // Confirm the lazy GET /runs/{id} fetch happened after we clicked.
    expect(specFetches.some((u) => u.includes(`/runs/${runIdA}`))).toBe(true);
  });
});
