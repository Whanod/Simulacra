import { test, expect } from "@playwright/test";

// US-016: the e2e test does not pin a fixed tab list. It discovers
// tabs from the live DOM and exercises every one so that backend
// category growth does not require a test rewrite.

test.describe("registry", () => {
  test("renders every category tab with entries", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/registry");
    await expect(page.getByTestId("registry-grid")).toBeVisible();

    // Discover every tab button inside the Tabs component. We lean
    // on the single `.tabs` container the registry page renders.
    const tabButtons = page.locator(".tabs button");
    const tabCount = await tabButtons.count();
    expect(
      tabCount,
      "registry page should render at least one category tab",
    ).toBeGreaterThan(0);

    for (let i = 0; i < tabCount; i++) {
      const tab = tabButtons.nth(i);
      const label = await tab.innerText();
      await tab.click();
      await expect(page.getByTestId("registry-grid")).toBeVisible();
      await expect(
        page.getByTestId("registry-entry").first(),
        `category tab "${label}" should have at least one entry`,
      ).toBeVisible();
    }

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("Start-from-this seeds the builder URL", async ({ page }) => {
    await page.goto("/registry?tab=reg-markets");
    const firstEntry = page.getByTestId("registry-entry").first();
    await expect(firstEntry).toBeVisible();

    // US-016: the test reads the raw backend `type` from the
    // registry entry, not the human label. This lets the builder
    // seed any market type the backend ships without the test
    // pinning a closed set.
    const entryType = await firstEntry.getAttribute("data-entry-type");
    expect(entryType, "entry should expose its backend type").toBeTruthy();

    await firstEntry.getByRole("button", { name: "Start from this" }).click();
    await expect(page).toHaveURL(
      new RegExp(
        `/builder\\?seed=${encodeURIComponent(`reg-markets:${entryType}`)}`,
      ),
    );
    await expect(page.getByText("Simulation Configuration")).toBeVisible();

    // The market Type dropdown is a RegistrySelect (US-013) so it
    // takes backend-driven options. Any seeded entry type that
    // matches a backend market type should land as the selected
    // value; the test does not hardcode which ones the backend ships.
    const marketType = await page.getByLabel("Market Type").inputValue();
    expect(marketType.length).toBeGreaterThan(0);
  });
});
