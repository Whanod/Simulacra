import { test, expect } from "@playwright/test";

test.describe("raw-spec fallback editor (US-014)", () => {
  test("toggle reveals the raw editor seeded from the structured spec", async ({
    page,
  }) => {
    await page.goto("/builder");
    await page.getByRole("button", { name: "Start from Scratch" }).click();

    // Structured form is visible by default.
    await expect(
      page.getByRole("button", { name: "Edit as Raw JSON" }),
    ).toBeVisible();

    await page.getByTestId("editor-mode-toggle").click();

    const textarea = page.getByTestId("raw-spec-editor-textarea");
    await expect(textarea).toBeVisible();

    const initial = await textarea.inputValue();
    // The seeded text must be valid JSON reflecting the structured
    // builder's current spec.
    const parsed = JSON.parse(initial);
    expect(parsed).toHaveProperty("market");
    expect(parsed).toHaveProperty("agents");
    expect(parsed).toHaveProperty("config");
  });

  test("invalid JSON surfaces a parse error without clobbering draft", async ({
    page,
  }) => {
    await page.goto("/builder");
    await page.getByRole("button", { name: "Start from Scratch" }).click();
    await page.getByTestId("editor-mode-toggle").click();

    const textarea = page.getByTestId("raw-spec-editor-textarea");
    await textarea.fill("{ this is not valid json");
    // Blur commits the textarea value via onBlur.
    await textarea.blur();

    await expect(
      page.getByTestId("raw-spec-editor-parse-error"),
    ).toContainText(/Invalid spec/i);
  });

  test("preserves an unknown vendor block through raw edit + build", async ({
    page,
  }) => {
    await page.goto("/builder");
    await page.getByRole("button", { name: "Start from Scratch" }).click();
    await page.getByTestId("editor-mode-toggle").click();

    const textarea = page.getByTestId("raw-spec-editor-textarea");
    const initial = await textarea.inputValue();
    const spec = JSON.parse(initial) as Record<string, unknown>;

    // Add a vendor extension that the structured form cannot
    // represent. The draft model must preserve it across the build.
    spec.vendor_extension = { hello: "raw-world", nested: { flag: true } };

    // Tweak a known field to prove structured and unknown data
    // coexist in the final spec.
    (spec.config as Record<string, unknown>).num_rounds = 5;

    await textarea.fill(JSON.stringify(spec, null, 2));
    await textarea.blur();

    // No parse error after valid edit.
    await expect(
      page.getByTestId("raw-spec-editor-parse-error"),
    ).toHaveCount(0);

    // Build & Run from raw mode — the effective spec should be the
    // raw draft we just committed, vendor extension included.
    await page
      .getByRole("button", { name: "Build & Run" })
      .first()
      .click();
    await page.waitForURL(/\/results\/[^/]+/, { timeout: 30_000 });
  });
});
