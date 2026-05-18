import {
  test,
  expect,
  type Locator,
  type Response,
} from "@playwright/test";

const CORPUS_REPLAY_SLOT = 420_196_842;
const REPLAY_METRIC_KEYS = [
  "bundle_landing_rate",
  "tip_efficiency",
  "slot_inclusion_latency",
  "cu_per_dollar_tip_breakeven",
  "skip_rate_cost",
  "write_lock_heatmap",
  "submission_path_comparison",
] as const;

async function setNativeInputValue(locator: Locator, value: string) {
  await locator.evaluate((node, nextValue) => {
    const input = node as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set;
    setter?.call(input, nextValue);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

test.describe("replay page", () => {
  test("loads_slot_and_re_runs_with_counterfactual", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/replay");
    await expect(page.getByTestId("replay-page")).toBeVisible();

    await page.getByTestId("replay-slot-search").fill("420196842");
    await page.getByTestId("replay-slot-apply").click();
    await expect(page.getByTestId("replay-slot-start")).toHaveValue("420196842");
    await expect(page.getByTestId("replay-slot-end")).toHaveValue("420196842");

    await page.getByTestId("replay-tip-bundle-id").fill("sig-250m");
    await setNativeInputValue(page.getByTestId("replay-tip-slider"), "100000");
    await expect(page.getByTestId("replay-tip-new-lamports")).toHaveValue(
      "100000",
    );

    const replayResponses: Response[] = [];
    const twoReplayResponses = new Promise<Response[]>((resolve) => {
      const handler = (response: Response) => {
        if (
          response.url().endsWith("/v1/replay") &&
          response.request().method() === "POST"
        ) {
          replayResponses.push(response);
          if (replayResponses.length === 2) {
            page.off("response", handler);
            resolve(replayResponses);
          }
        }
      };
      page.on("response", handler);
    });
    const started = performance.now();
    await page.getByTestId("replay-submit").click();
    const responses = await twoReplayResponses;
    const elapsedMs = performance.now() - started;

    for (const response of responses) {
      expect(response.ok()).toBeTruthy();
    }
    expect(elapsedMs).toBeLessThan(10_000);

    await expect(page.getByTestId("replay-result")).toBeVisible();
    await expect(page.getByTestId("replay-side-by-side")).toBeVisible();
    await expect(page.getByTestId("replay-diff-column")).toBeVisible();
    await expect(
      page.getByTestId("replay-diff-column").getByTestId("replay-diff-metric"),
    ).toHaveCount(7);
    for (const key of REPLAY_METRIC_KEYS) {
      await expect(
        page
          .getByTestId("replay-diff-column")
          .locator(`[data-metric-key="${key}"]`),
      ).toBeVisible();
    }
    await expect(page.getByTestId("replay-diff-column")).not.toContainText(
      "Decoded coverage",
    );
    await expect(page.getByTestId("replay-service-response")).toContainText(
      "420196842",
    );
    await expect(page.getByTestId("replay-service-response")).toContainText(
      "TipReplaceCounterfactual",
    );

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("corpus_slot_uses_canonical_metrics_without_calibration_claim", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/replay");
    await expect(page.getByTestId("replay-page")).toBeVisible();

    const corpusSlot = page
      .getByTestId("replay-famous-slot")
      .filter({ hasText: String(CORPUS_REPLAY_SLOT) });
    await expect(corpusSlot).toBeVisible();
    await corpusSlot.click();
    await expect(page.getByTestId("replay-share-scope")).toContainText(
      "Permanent for curated corpus slots.",
    );

    await page.getByTestId("replay-tip-bundle-id").fill("sig-250m");
    await setNativeInputValue(page.getByTestId("replay-tip-slider"), "100000");
    await page.getByTestId("replay-submit").click();

    await expect(page.getByTestId("replay-result")).toBeVisible();
    await expect(page.getByTestId("replay-diff-column")).toContainText(
      "partial",
    );
    await expect(
      page.getByTestId("replay-diff-column").getByTestId("replay-diff-metric"),
    ).toHaveCount(7);
    await expect(page.locator('[data-testid="calibration-band"]')).toHaveCount(0);
    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("tip_pnl_chart_renders_without_route_injected_calibration", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/replay");
    await expect(page.getByTestId("replay-page")).toBeVisible();

    await page.getByTestId("replay-slot-search").fill(String(CORPUS_REPLAY_SLOT));
    await page.getByTestId("replay-slot-apply").click();
    await page.getByTestId("replay-tip-bundle-id").fill("sig-tip-pnl");
    await setNativeInputValue(page.getByTestId("replay-tip-slider"), "100000");
    await page.getByTestId("replay-submit").click();

    const tipPnlPanel = page.getByTestId("tip-pnl-sweep-panel");
    await expect(tipPnlPanel).toBeVisible();
    await expect(
      tipPnlPanel.getByRole("img", { name: "Tip versus PnL sweep" }),
    ).toBeVisible();

    const calibrationBand = tipPnlPanel.locator(
      '[data-testid="calibration-band"]',
    );
    await expect(calibrationBand).toHaveCount(0);

    await expect(tipPnlPanel.locator(".tip-pnl-mainnet-point")).toHaveCount(0);
    await expect(tipPnlPanel.locator(".tip-pnl-active-tip")).toHaveCount(1);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("share_link_round_trips_state", async ({ page, browser }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto("/replay");
    await expect(page.getByTestId("replay-page")).toBeVisible();

    await page.getByTestId("replay-slot-search").fill("420196842");
    await page.getByTestId("replay-slot-apply").click();
    await expect(page.getByTestId("replay-slot-start")).toHaveValue("420196842");
    await expect(page.getByTestId("replay-slot-end")).toHaveValue("420196842");

    await page.getByTestId("replay-tip-bundle-id").fill("sig-share-link");
    await setNativeInputValue(page.getByTestId("replay-tip-slider"), "180000");
    await expect(page.getByTestId("replay-tip-new-lamports")).toHaveValue(
      "180000",
    );

    await page.getByTestId("replay-fee-toggle").check();
    await page.getByTestId("replay-fee-pool").fill("whirlpool-sol-usdc");
    await setNativeInputValue(page.getByTestId("replay-fee-bps-slider"), "45");
    await expect(page.getByTestId("replay-fee-bps-input")).toHaveValue("45");

    await page.getByTestId("replay-ordering-toggle").check();
    await page.getByTestId("replay-scheduler-select").selectOption("serial");

    await page.getByTestId("replay-agent-toggle").check();
    await page.getByTestId("replay-agent-id").fill("jito-share-agent");
    await page.getByTestId("replay-agent-strategy").selectOption("sandwich");
    await setNativeInputValue(
      page.getByTestId("replay-agent-min-ev-slider"),
      "250000",
    );
    await expect(page.getByTestId("replay-agent-min-ev-input")).toHaveValue(
      "250000",
    );
    await page.getByTestId("replay-agent-tip-account").fill(
      "share-tip-account",
    );

    const shareInput = page.getByTestId("replay-share-url");
    await expect(shareInput).toHaveValue(/\/replay\?/);
    const sourceShareUrl = new URL(await shareInput.inputValue(), page.url());
    expect(sourceShareUrl.searchParams.get("slot")).toBe("420196842");
    const encodedCounterfactuals = sourceShareUrl.searchParams.get("cf");
    expect(encodedCounterfactuals).not.toBeNull();
    const counterfactuals = JSON.parse(encodedCounterfactuals ?? "[]") as Array<{
      kind: string;
      params: Record<string, unknown>;
    }>;
    expect(counterfactuals.map((spec) => spec.kind)).toEqual([
      "TipReplaceCounterfactual",
      "FeeReplaceCounterfactual",
      "OrderingReplaceCounterfactual",
      "AgentInjectCounterfactual",
    ]);

    const sharedContext = await browser.newContext();
    try {
      const sharedPage = await sharedContext.newPage();
      const sharedConsoleErrors: string[] = [];
      sharedPage.on("console", (msg) => {
        if (msg.type() === "error") sharedConsoleErrors.push(msg.text());
      });

      await sharedPage.goto(sourceShareUrl.toString());
      await expect(sharedPage.getByTestId("replay-page")).toBeVisible();
      await expect(sharedPage.getByTestId("replay-share-notice")).toContainText(
        "Shared replay state loaded.",
      );

      await expect(sharedPage.getByTestId("replay-slot-start")).toHaveValue(
        "420196842",
      );
      await expect(sharedPage.getByTestId("replay-slot-end")).toHaveValue(
        "420196842",
      );
      await expect(sharedPage.getByTestId("replay-slot-search")).toHaveValue(
        "420196842",
      );

      await expect(sharedPage.getByTestId("replay-tip-toggle")).toBeChecked();
      await expect(sharedPage.getByTestId("replay-tip-bundle-id")).toHaveValue(
        "sig-share-link",
      );
      await expect(sharedPage.getByTestId("replay-tip-new-lamports")).toHaveValue(
        "180000",
      );

      await expect(sharedPage.getByTestId("replay-fee-toggle")).toBeChecked();
      await expect(sharedPage.getByTestId("replay-fee-pool")).toHaveValue(
        "whirlpool-sol-usdc",
      );
      await expect(sharedPage.getByTestId("replay-fee-bps-input")).toHaveValue(
        "45",
      );

      await expect(
        sharedPage.getByTestId("replay-ordering-toggle"),
      ).toBeChecked();
      await expect(sharedPage.getByTestId("replay-scheduler-select")).toHaveValue(
        "serial",
      );

      await expect(sharedPage.getByTestId("replay-agent-toggle")).toBeChecked();
      await expect(sharedPage.getByTestId("replay-agent-id")).toHaveValue(
        "jito-share-agent",
      );
      await expect(sharedPage.getByTestId("replay-agent-strategy")).toHaveValue(
        "sandwich",
      );
      await expect(sharedPage.getByTestId("replay-agent-min-ev-input")).toHaveValue(
        "250000",
      );
      await expect(sharedPage.getByTestId("replay-agent-tip-account")).toHaveValue(
        "share-tip-account",
      );

      await expect(sharedPage.getByTestId("replay-request-preview")).toContainText(
        "AgentInjectCounterfactual",
      );
      expect(
        sharedConsoleErrors,
        `unexpected shared-page console errors: ${sharedConsoleErrors.join(" | ")}`,
      ).toEqual([]);
    } finally {
      await sharedContext.close();
    }

    expect(
      consoleErrors,
      `unexpected source-page console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
