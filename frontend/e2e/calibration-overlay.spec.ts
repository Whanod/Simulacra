// PRD US-004 line 821: replay artifacts may carry replay_diff, but calibration
// bands must not render unless the backend artifact itself carries a real
// mainnet_accuracy_claim.

import { test, expect } from "@playwright/test";
import { seedReplayRun, API_BASE } from "./_helpers";

test.describe("results › calibration overlay", () => {
  test("backend_replay_diff_does_not_render_without_accuracy_claim", async ({
    page,
    request,
  }) => {
    const runId = await seedReplayRun(request);
    const resultResponse = await request.get(`${API_BASE}/runs/${runId}/result`);
    expect(resultResponse.ok()).toBeTruthy();
    const resultBody = (await resultResponse.json()) as {
      result: { replay_diff?: { per_metric_error?: Record<string, unknown> } };
    };
    expect(resultBody.result.replay_diff?.per_metric_error?.tips_paid).toBeTruthy();

    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto(`/results/${runId}?tab=charts`);

    await expect(page.getByText("Cumulative Fees")).toBeVisible();

    const bands = page.locator('[data-testid="calibration-band"]');
    await expect(bands).toHaveCount(0);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
