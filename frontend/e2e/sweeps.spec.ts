import { test, expect } from "@playwright/test";
import { seedSweep } from "./_helpers";

test.describe("sweeps", () => {
  let sweepId = "";

  test.beforeAll(async ({ request }) => {
    sweepId = await seedSweep(request);
  });

  test("list page shows the seeded sweep and links to detail", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/sweeps");
    const row = page.locator(`[data-sweep-id="${sweepId}"]`);
    await expect(row).toBeVisible();
    await row.click();
    await expect(page).toHaveURL(new RegExp(`/sweeps/${sweepId}$`));

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("detail page renders heatmap, top configs, sensitivity, and robustness", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto(`/sweeps/${sweepId}`);

    await expect(page.getByTestId("sweep-heatmap")).toBeVisible();
    await expect(page.getByTestId("sweep-top-configs")).toBeVisible();
    await expect(page.getByTestId("sweep-sensitivity")).toBeVisible();
    await expect(page.getByTestId("sweep-robustness")).toBeVisible();

    // Top configs should have at least one row.
    await expect(
      page.locator('[data-testid="sweep-top-configs"] tbody tr').first(),
    ).toBeVisible();

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("promoting a top config seeds the builder form", async ({ page }) => {
    await page.goto(`/sweeps/${sweepId}`);
    await page
      .locator('[data-testid="sweep-top-configs"] tbody tr')
      .first()
      .getByRole("button", { name: "Promote" })
      .click();

    await expect(page).toHaveURL(/\/builder$/);
    await expect(page.getByText("Simulation Configuration")).toBeVisible();
    await expect(page.getByLabel("Rounds")).toHaveValue("4");
  });
});
