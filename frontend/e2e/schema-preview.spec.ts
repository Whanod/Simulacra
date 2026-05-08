import { test, expect } from "@playwright/test";

test.describe("schema renderer preview", () => {
  test("renders a generic form per registry category with scalar, enum, and boolean inputs", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/builder/schema-preview");

    // Topbar title is rendered server-side.
    await expect(
      page.getByRole("heading", { name: "Schema Renderer Preview" }),
    ).toBeVisible();

    // Wait for the contract fetch to complete and at least one form to mount.
    await expect(page.locator("form.schema-form-root").first()).toBeVisible({
      timeout: 15_000,
    });

    // Confirm the renderer produced several forms (one per category).
    const formCount = await page.locator("form.schema-form-root").count();
    expect(formCount).toBeGreaterThanOrEqual(3);

    // Scope subsequent assertions to a single card so we can correlate
    // the input we edit with the JSON preview that reflects it.
    const firstCard = page
      .locator(".card")
      .filter({ has: page.locator("form.schema-form-root") })
      .first();

    // Confirm at least one labeled number input renders (scalar path).
    const numberInputs = firstCard.locator(
      'form.schema-form-root input[type="number"]',
    );
    expect(await numberInputs.count()).toBeGreaterThan(0);

    // Type-coerce a numeric field and confirm the JSON params block
    // reflects the new value — exercises onChange → updateDraft → rerender.
    const inputEl = firstCard
      .locator('form.schema-form-root input[type="number"]')
      .first();
    await inputEl.fill("7777");
    await inputEl.blur();
    const details = firstCard.locator("details");
    await details.click();
    await expect(details).toContainText("7777");

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("renders the noop-preview fixture plugin from the special-editor registry", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/builder/schema-preview");
    await expect(page.locator("form.schema-form-root").first()).toBeVisible({
      timeout: 15_000,
    });

    // The preview page seeds a synthetic entity pointing at the
    // noop-preview plugin (US-010 fixture). The SchemaForm detects
    // `uiSchema.specialEditor` and routes through the registry
    // instead of rendering the generic form.
    const pluginForm = page.locator(
      'form.schema-form-root[data-special-editor-key="noop-preview"]',
    );
    await expect(pluginForm).toBeVisible();
    await expect(
      pluginForm.locator('[data-special-editor="noop-preview"]'),
    ).toBeVisible();
    await expect(pluginForm.getByText(/Plugin:\s*noop-preview/)).toBeVisible();
    await expect(
      pluginForm.getByRole("button", { name: "Edit raw" }),
    ).toBeVisible();

    // Clicking "Edit raw" swaps in the RawJsonEditor fallback, so
    // plugins without a custom UI still let the user make changes.
    await pluginForm.getByRole("button", { name: "Edit raw" }).click();
    await expect(
      pluginForm.getByRole("button", { name: "Close raw" }),
    ).toBeVisible();
    await expect(pluginForm.locator("textarea")).toBeVisible();

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("world-markets-graph plugin edits topology through the draft model", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/builder/schema-preview");
    await expect(page.locator("form.schema-form-root").first()).toBeVisible({
      timeout: 15_000,
    });

    // The `markets:world` entity ships `specialEditor: "world-markets-graph"`;
    // the preview seeds it with two markets + one link so the graph
    // mounts with visible content.
    const worldForm = page.locator(
      'form.schema-form-root[data-special-editor-key="world-markets-graph"]',
    );
    await expect(worldForm).toBeVisible();
    await expect(
      worldForm.locator('[data-special-editor="world-markets-graph"]'),
    ).toBeVisible();

    // Canvas starts with 2 markets.
    const markets = worldForm.locator("[data-market-id]");
    await expect(markets).toHaveCount(2);

    // Click "+ CLOB" to add a third market — writes back through the
    // onChange path into the draft params.
    await worldForm.locator('[data-action="add-clob"]').click();
    await expect(markets).toHaveCount(3);

    // The preview shows the updated params in the <details> block;
    // opening it should reveal the new market.
    const card = page
      .locator(".card")
      .filter({ has: worldForm });
    const details = card.locator("details");
    await details.click();
    await expect(details).toContainText('"markets"');
    // Three ids: m1, m2, and the newly minted m3.
    await expect(details).toContainText('"m3"');

    // Remove the m3 market again and confirm the canvas and the draft
    // are back to two markets.
    const removeBtn = worldForm.getByRole("button", { name: /Remove .*m3/ });
    await removeBtn.click();
    await expect(markets).toHaveCount(2);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("renders section headers, widget hints, and a basic/advanced toggle", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/builder/schema-preview");
    await expect(page.locator("form.schema-form-root").first()).toBeVisible({
      timeout: 15_000,
    });

    // Section headers: the backend ships at least one entity with
    // named sections (agents/noise has "Trade Sizing"/"Behavior" etc.,
    // gas_models/eip1559 has "Basic"/"Advanced"). At least one <h4>
    // inside a .form-section must render across the previews.
    const sectionHeaders = page.locator(".form-section h4");
    expect(await sectionHeaders.count()).toBeGreaterThan(0);

    // Widget hints: a slider and a range input should render for the
    // numeric fields that carry `widget: "slider"` in the contract.
    const sliders = page.locator('form.schema-form-root input[type="range"]');
    expect(await sliders.count()).toBeGreaterThan(0);

    // Basic/advanced toggle: target the agents / Noise Trader card
    // specifically, because it ships `sections` with an `advanced`
    // level in the contract (BE-005). That gives us a deterministic
    // entity whose toggle we know exists.
    const noiseCard = page
      .locator(".card")
      .filter({ hasText: "agents / Noise Trader" });
    await expect(noiseCard).toBeVisible();
    const toggle = noiseCard.getByRole("button", {
      name: /Show advanced|Hide advanced/,
    });
    await expect(toggle).toHaveText("Show advanced");
    // Advanced section is hidden before click.
    const advancedSection = noiseCard.locator(
      '.form-section[data-level="advanced"]',
    );
    expect(await advancedSection.count()).toBe(0);
    await toggle.click();
    await expect(toggle).toHaveText("Hide advanced");
    expect(await advancedSection.count()).toBeGreaterThan(0);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
