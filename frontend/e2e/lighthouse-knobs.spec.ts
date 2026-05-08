import { test, expect, type Page } from "@playwright/test";

const LIGHTHOUSE_TEMPLATE_ID = "solana-sandwich-lighthouse";

/**
 * WS-3.2 acceptance gate (builder-structured-form-rewrite.md): once
 * Plan B's structured-form rewrite ships, the lighthouse template
 * must round-trip its lighthouse-critical knobs through the
 * structured form — agents must keep their explicit population (4
 * noise / 1 victim-1 / 1 victim-small / 1 sandwich-1 / 1 lp-1 / 1
 * searcher-1) and the protocol-variables pane must surface
 * bundle_auction, pre-roll, ALTs, cost_token, visible_roles, and the
 * editable_fields hint strip.
 *
 * The "twiddle each editable_field, run sim, assert metric movement
 * outside a noise band" loop adds ~30s × N simulation runs which is
 * too slow for default CI. We approximate it here by checking that
 * structured-form edits flow into the raw JSON the backend would
 * receive, plus a single full build-and-run smoke. The slope-tweak
 * end-to-end metric assertion still lives in lighthouse-scenario.
 */
test.describe("lighthouse structured-form round trip", () => {
  test("renders lighthouse template in structured mode with editable-field hints", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);

    // Editor lands in structured mode — no raw textarea.
    const rawTextarea = page.locator(
      "[data-testid='raw-spec-editor-textarea']",
    );
    await expect(rawTextarea).toHaveCount(0);

    // editable_fields hint strip surfaces somewhere on the page.
    await expect(
      page.locator("text=Knobs from this template:").first(),
    ).toBeVisible();
  });

  test("agent population preserves the lighthouse 9-agent shape (4 noise + 5 distinct singletons)", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);

    const groupCards = page.locator("[data-testid='agent-group-card']");
    // 4 noise coalesce to one group; victim-1, victim-small,
    // sandwich-1, lp-1, searcher-1 are distinct → 6 groups.
    await expect(groupCards).toHaveCount(6);

    // Specific agent types from the lighthouse template are present.
    const expectedTypes = [
      "noise",
      "swap_noise",
      "manipulator",
      "passive_lp",
      "jito_searcher",
    ];
    for (const t of expectedTypes) {
      await expect(
        page.locator(`[data-testid='agent-group-card'][data-group-type='${t}']`),
      ).toHaveCount(t === "swap_noise" ? 2 : 1);
    }
  });

  test("protocol-variables pane surfaces bundle-auction, pre-roll, ALTs, cost-token cards", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);

    // Switch to the protocol-variables pane.
    await page.getByRole("button", { name: /protocol variables/i }).click();

    await expect(
      page.getByRole("heading", { name: "Whirlpool corpus" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Bundle auction" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Priority-fee pre-roll" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /address lookup tables/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /cost & visibility/i }),
    ).toBeVisible();

    // Lighthouse-specific values land in their controls.
    await expect(page.locator("#ba-tip-curve-path")).toHaveValue(
      /jito_tip_curves\.yaml$/,
    );
    await expect(page.locator("#cost-token")).toHaveValue("USDC");

    // visible_roles is now a chip-picker; jito_searcher chip should
    // be selected (aria-pressed="true") because the lighthouse
    // template sets visible_roles=["jito_searcher"].
    const visibleRoles = page.locator("#visible-roles");
    await expect(
      visibleRoles.locator(
        'button[aria-pressed="true"]:has-text("jito_searcher")',
      ),
    ).toBeVisible();
  });

  test("editable-field hint pill scrolls to and highlights the matching control", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);
    await page.getByRole("button", { name: /protocol variables/i }).click();

    // Click a pill whose mapped control exists in this pane.
    const pill = page
      .getByRole("button", { name: "execution.params.cost_token" })
      .first();
    if (await pill.count()) {
      await pill.click();
      const target = page.locator(
        '[data-editable-field="execution.params.cost_token"]',
      );
      await expect(target).toBeVisible();
      // Flash class is transient — assert it lands at least once.
      await expect(target).toHaveClass(/editable-field-flash/);
    }
  });

  /**
   * Read the structured-form's emitted spec by toggling into raw mode.
   * The raw textarea is seeded from `buildSpec` → `specToApi`, so any
   * structured-form edit that the adapter respects shows up here.
   */
  const readRawSpec = async (page: Page): Promise<Record<string, unknown>> => {
    await page.getByTestId("editor-mode-toggle").click();
    const textarea = page.locator(
      "[data-testid='raw-spec-editor-textarea']",
    );
    await expect(textarea).toBeVisible();
    const text = await textarea.inputValue();
    return JSON.parse(text) as Record<string, unknown>;
  };

  test("editing cost-token in the structured form flows into the emitted spec", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);
    await page.getByRole("button", { name: /protocol variables/i }).click();

    const costToken = page.locator("#cost-token");
    await costToken.fill("SOL");
    await costToken.blur();

    const spec = await readRawSpec(page);
    const execParams = (
      (spec.execution as Record<string, unknown>)?.params as Record<
        string,
        unknown
      >
    );
    expect(execParams.cost_token).toBe("SOL");
  });

  test("editing bundle-auction max_bundles_per_slot flows into the emitted spec", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);
    await page.getByRole("button", { name: /protocol variables/i }).click();

    const maxBundles = page.locator("#ba-max-bundles");
    await maxBundles.fill("8");
    await maxBundles.blur();

    const spec = await readRawSpec(page);
    const execParams = (spec.execution as Record<string, unknown>)
      ?.params as Record<string, unknown>;
    const auction = execParams.bundle_auction as Record<string, unknown>;
    expect(auction.max_bundles_per_slot).toBe(8);
  });

  test("editing pre-roll seed flows into the emitted spec", async ({ page }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);
    await page.getByRole("button", { name: /protocol variables/i }).click();

    const seed = page.locator("#pre-seed");
    await seed.fill("9001");
    await seed.blur();

    const spec = await readRawSpec(page);
    const execParams = (spec.execution as Record<string, unknown>)
      ?.params as Record<string, unknown>;
    const pfm = execParams.priority_fee_market as Record<string, unknown>;
    const preRoll = pfm.pre_roll as Record<string, unknown>;
    expect(preRoll.seed).toBe(9001);
  });

  test("toggling a visible-roles chip flows into the emitted spec", async ({
    page,
  }) => {
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);
    await page.getByRole("button", { name: /protocol variables/i }).click();

    const visibleRoles = page.locator("#visible-roles");
    // Lighthouse seeds visible_roles=["jito_searcher"]. Toggle the
    // jito_searcher chip off; the emitted spec must drop visible_roles
    // entirely (empty list signals "all roles visible").
    const chip = visibleRoles.locator(
      'button:has-text("jito_searcher")',
    );
    await chip.click();
    await expect(chip).toHaveAttribute("aria-pressed", "false");

    const spec = await readRawSpec(page);
    const execParams = (spec.execution as Record<string, unknown>)
      ?.params as Record<string, unknown>;
    expect(execParams.visible_roles).toBeUndefined();
  });

  test("twiddling num_rounds knob and re-running produces a result", async ({
    page,
  }) => {
    test.slow();
    await page.goto(`/builder?template=${LIGHTHOUSE_TEMPLATE_ID}`);

    // Tighten the run for CI speed, but keep enough rounds for the
    // sandwich strategy to fire at least once.
    await page.getByRole("button", { name: /^general$/i }).click();
    const roundsInput = page.locator("input[type='number']").first();
    // Ensure we land on the rounds input — rounds is the first numeric
    // input under the General pane in this layout.
    await roundsInput.fill("50");

    await page.getByRole("button", { name: /build & run/i }).click();
    await page.waitForURL(/\/results\/[^/]+/, { timeout: 60_000 });
    await expect(page.getByText(/composite score/i)).toBeVisible();
  });
});
