import { test, expect } from "@playwright/test";

test.describe("builder", () => {
  test("Build & Run on the default form redirects to a results page", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/builder");

    // Step 1: template picker — skip to the form with default values.
    await page.getByRole("button", { name: "Start from Scratch" }).click();

    // Kick off a sync build. Defaults are valid (mix sums to 100, rounds > 0).
    await page.getByRole("button", { name: "Build & Run" }).click();

    // Should land on /results/{id} after the sync build completes.
    await page.waitForURL(/\/results\/[^/]+/, { timeout: 30_000 });

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("Build & Run with invalid mix surfaces inline validation errors", async ({
    page,
  }) => {
    await page.goto("/builder");
    await page.getByRole("button", { name: "Start from Scratch" }).click();

    // Zero out the Rounds input — that pushes `numRounds` to 0 which fails
    // the frontend pre-flight before any backend call.
    const roundsInput = page.locator("input[type=number]").nth(2);
    await roundsInput.fill("0");

    await page.getByRole("button", { name: "Build & Run" }).click();

    // Inline validation panel renders instead of a toast-only failure.
    await expect(page.getByTestId("builder-validation-errors")).toBeVisible();

    // URL should not advance.
    await expect(page).toHaveURL(/\/builder$/);
  });
});
