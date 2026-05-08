import { test, expect } from "@playwright/test";

test.describe("bundle simulator", () => {
  test("paste_bundle_get_landing_probability", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/bundle-simulator");
    await expect(page.getByTestId("bundle-simulator-page")).toBeVisible();

    await page.getByTestId("bundle-paste-box").fill("base58encodedtx1");
    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/v1/simulate-bundle") &&
        response.request().method() === "POST",
    );
    await page.getByTestId("bundle-run-button").click();
    const response = await responsePromise;
    expect(response.ok()).toBeTruthy();
    const apiResponse = await response.json();

    await expect(page.getByTestId("bundle-result-panel")).toBeVisible();
    await expect(page.getByTestId("bundle-landing-probability")).toHaveText("99.0%");
    await expect(page.getByTestId("bundle-api-response")).toContainText(
      "landing_probability",
    );
    const renderedResponseText = await page
      .getByTestId("bundle-api-response")
      .locator("pre")
      .textContent();
    expect(JSON.parse(renderedResponseText ?? "{}")).toEqual(apiResponse);
    await expect(page.getByTestId("bundle-calibration-band").first()).toBeVisible();

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("tip_slider_updates_landing_probability", async ({ page }) => {
    await page.goto("/bundle-simulator");

    await page.getByTestId("bundle-tip-input").fill("1000");
    await page.getByTestId("bundle-run-button").click();
    await expect(page.getByTestId("bundle-landing-probability")).toHaveText("50.0%");

    await page.getByTestId("bundle-tip-slider").evaluate((node) => {
      const input = node as HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setter?.call(input, "2000");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await expect(page.getByTestId("bundle-tip-input")).toHaveValue("2000");
    await page.getByTestId("bundle-run-button").click();

    await expect(page.getByTestId("bundle-landing-probability")).toHaveText("75.0%");
  });
});
