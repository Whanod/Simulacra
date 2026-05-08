import { test, expect } from "@playwright/test";

const LIGHTHOUSE_TEMPLATE_ID = "solana-sandwich-lighthouse";

test.describe("lighthouse scenario", () => {
  test("test_dashboard_features_lighthouse_template", async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator("[data-testid='template-card']").first().waitFor();

    const card = page.locator(
      `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
    );
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-featured", "true");

    const ribbon = card.locator('[data-testid="template-featured-ribbon"]');
    await expect(ribbon).toBeVisible();
    await expect(ribbon).toHaveText(/featured demo/i);
  });

  test("test_lighthouse_help_panel_reachable_from_card", async ({ page }) => {
    await page.goto("/dashboard");
    await page.locator("[data-testid='template-card']").first().waitFor();

    const card = page.locator(
      `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
    );
    await expect(card).toBeVisible();

    const helpLink = card.locator('[data-testid="template-what-this-is-link"]');
    await expect(helpLink).toBeVisible();
    await expect(helpLink).toHaveAttribute("href", "/help/lighthouse-scenario");

    await helpLink.click();
    await page.waitForURL("**/help/lighthouse-scenario");
    await expect(
      page.locator('[data-help-page="lighthouse-scenario"]'),
    ).toBeVisible();
  });

  test("test_lighthouse_runs_with_defaults_and_renders_results", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    await page.locator("[data-testid='template-card']").first().waitFor();

    const card = page.locator(
      `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
    );
    await expect(card).toBeVisible();
    await card.click();

    await page.waitForURL(/\/builder\?template=/);
    const buildAndRun = page.getByRole("button", { name: /build & run/i });
    await expect(buildAndRun).toBeVisible();
    await buildAndRun.click();

    await page.waitForURL(/\/results\/[^/]+/, { timeout: 45_000 });
    await expect(page.getByText("Max Drawdown")).toBeVisible();
    await expect(page.getByText("Composite Score")).toBeVisible();
  });

  test("test_lighthouse_parameter_change_triggers_visible_diff", async ({
    page,
  }) => {
    // PRD US-002 / PRD line 121: change tip-curve slope, re-run, observe a
    // measurable change in landing rate. The slope lives at
    // agents[*].params.tip_curve.slope_micro_lamports_per_ev — a nested
    // object in the JitoSearcher's params block. The structured form
    // can't represent it, so applying the lighthouse template auto-enters
    // raw mode (templates.ts: requiresRawSpec) and the textarea contains
    // the verbatim backend base_spec.
    const runOnce = async (slopeOverride: number | null): Promise<{
      landingRate: string;
      tipRoi: string;
    }> => {
      await page.goto("/dashboard");
      await page.locator("[data-testid='template-card']").first().waitFor();
      const card = page.locator(
        `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
      );
      await expect(card).toBeVisible();
      await card.click();
      await page.waitForURL(/\/builder\?template=/);

      if (slopeOverride !== null) {
        const textarea = page.locator(
          "[data-testid='raw-spec-editor-textarea']",
        );
        await expect(textarea).toBeVisible();
        const currentText = await textarea.inputValue();
        const modifiedText = currentText.replace(
          /"slope_micro_lamports_per_ev":\s*[0-9.]+/,
          `"slope_micro_lamports_per_ev": ${slopeOverride}`,
        );
        expect(modifiedText).not.toBe(currentText);
        await textarea.fill(modifiedText);
        // Commit on blur per RawSpecEditor contract.
        await textarea.blur();
      }

      await page.getByRole("button", { name: /build & run/i }).click();
      await page.waitForURL(/\/results\/[^/]+/, { timeout: 45_000 });

      const solanaTab = page.locator(".tabs button", { hasText: /^Solana$/ });
      await expect(solanaTab).toBeVisible();
      await solanaTab.click();

      const jitoCard = page.locator("[data-testid='solana-jito-metrics']");
      await expect(jitoCard).toBeVisible();

      const readStatCard = async (label: string): Promise<string> => {
        const statCard = jitoCard
          .locator(".stat-card")
          .filter({
            has: page.locator(".label", { hasText: new RegExp(`^${label}$`) }),
          })
          .first();
        await expect(statCard).toBeVisible();
        return (await statCard.locator(".value").innerText()).trim();
      };

      return {
        landingRate: await readStatCard("Landing rate"),
        tipRoi: await readStatCard("Tip ROI"),
      };
    };

    // Default slope is 0.05; an order-of-magnitude tweak materially changes
    // the searcher's tip and therefore landing rate / tip ROI. Either
    // metric flipping is sufficient for the diff assertion.
    const baseline = await runOnce(null);
    const tweaked = await runOnce(0.5);

    expect(
      baseline.landingRate !== tweaked.landingRate ||
        baseline.tipRoi !== tweaked.tipRoi,
    ).toBe(true);
  });

  test("test_lighthouse_template_synthetic_mode_badge_is_hidden", async ({
    page,
  }) => {
    // The lighthouse template runs real Whirlpool CLMM math against a
    // captured mainnet pool (synthetic_mode=False). The template-level
    // "synthetic-math" badge should NOT appear on the template card.
    await page.goto("/dashboard");
    await page.locator("[data-testid='template-card']").first().waitFor();

    const card = page.locator(
      `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
    );
    await expect(card).toBeVisible();

    const cardBadge = card.locator('[data-synthetic-badge="true"]');
    await expect(cardBadge).toHaveCount(0);
  });

  test("test_lighthouse_jito_searcher_calibration_block_present", async ({
    page,
  }) => {
    // FIX-020: the JitoSearcher's landing-rate prior is now calibrated
    // against real mainnet Jito bundles via the fitted TipQuoteCurve in
    // solana-plans/calibration/jito_tip_curves.yaml. The lighthouse run
    // page MUST drop the "uncalibrated landing rate" badge and surface
    // a calibration footer naming the bundle count + capture date.
    await page.goto("/dashboard");
    await page.locator("[data-testid='template-card']").first().waitFor();

    const card = page.locator(
      `[data-testid="template-card"][data-template-id="${LIGHTHOUSE_TEMPLATE_ID}"]`,
    );
    await card.click();
    await page.waitForURL(/\/builder\?template=/);

    const buildAndRun = page.getByRole("button", { name: /build & run/i });
    await expect(buildAndRun).toBeVisible();
    await buildAndRun.click();

    await page.waitForURL(/\/results\/[^/]+/, { timeout: 45_000 });

    const solanaTab = page.locator(".tabs button", { hasText: /^Solana$/ });
    await expect(solanaTab).toBeVisible();
    await solanaTab.click();

    const solanaPanel = page.locator('[data-testid="results-solana-tab"]');
    await expect(solanaPanel).toBeVisible();

    const runBadge = solanaPanel.locator('[data-synthetic-marker="jito"]');
    await expect(runBadge).toHaveCount(0);

    const calibrationFooter = solanaPanel.locator(
      '[data-testid="jito-calibration-footer"]',
    );
    await expect(calibrationFooter).toBeVisible();
    await expect(calibrationFooter).toHaveAttribute(
      "data-jito-calibrated",
      "true",
    );
    await expect(calibrationFooter).toContainText(/Calibrated against/i);
    await expect(calibrationFooter).toContainText(/captured/i);
  });
});
