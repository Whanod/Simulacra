import { test, expect } from "@playwright/test";
import { seedRun } from "./_helpers";

test.describe("compare", () => {
  let runIdA = "";
  let runIdB = "";

  test.beforeAll(async ({ request }) => {
    runIdA = await seedRun(request, { seed: 111 });
    runIdB = await seedRun(request, { seed: 222 });
  });

  test("selecting two runs renders spec diff and metric deltas", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/compare");
    // Both cards should be in the selection grid.
    await expect(page.getByText(runIdA)).toBeVisible();
    await expect(page.getByText(runIdB)).toBeVisible();

    // Select both runs.
    await page.getByText(runIdA).click();
    await page.getByText(runIdB).click();

    // Spec Differences card should appear (even if the diff is empty).
    await expect(
      page.getByText(/Spec Differences \(\d+\)/),
    ).toBeVisible();

    // Price Comparison overlay card renders.
    await expect(page.getByText("Price Comparison (Overlay)")).toBeVisible();

    // Selection should be reflected in the URL as a deep link.
    await expect(page).toHaveURL(
      new RegExp(`/compare\\?.*a=${runIdA}.*b=${runIdB}`),
    );

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("deep link with ?a=...&b=... hydrates selection", async ({ page }) => {
    await page.goto(`/compare?a=${runIdA}&b=${runIdB}`);
    // Spec Differences card should render without any clicks — the URL
    // pre-seeded the comparison.
    await expect(
      page.getByText(/Spec Differences \(\d+\)/),
    ).toBeVisible();
  });
});
