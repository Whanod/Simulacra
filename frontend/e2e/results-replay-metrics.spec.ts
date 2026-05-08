import { test, expect } from "@playwright/test";
import { API_BASE, seedReplayRun } from "./_helpers";

const REPLAY_METRIC_TITLES = [
  "Bundle landing rate",
  "Tip efficiency",
  "Slot inclusion latency",
  "CU/$ tip break-even",
  "Skip-rate cost",
  "Write-lock contention",
  "Submission path comparison",
];

test.describe("results replay metrics", () => {
  test("all_seven_metrics_render_from_seeded_replay_artifact", async ({
    page,
    request,
  }) => {
    const runId = await seedReplayRun(request);

    const resultResponse = await request.get(`${API_BASE}/runs/${runId}/result`);
    expect(resultResponse.ok()).toBeTruthy();
    const resultBody = (await resultResponse.json()) as {
      result: { round_snapshots?: Array<{ metrics?: { replay?: unknown } }> };
    };
    const latestReplayMetrics = [...(resultBody.result.round_snapshots ?? [])]
      .reverse()
      .find((snapshot) => snapshot.metrics?.replay)?.metrics?.replay;
    expect(latestReplayMetrics).toBeTruthy();

    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await page.goto(`/results/${runId}?tab=charts`);

    const grid = page.getByTestId("replay-metrics-grid");
    await expect(grid).toBeVisible();
    await expect(grid.getByTestId("replay-chart-block")).toHaveCount(7);

    for (const title of REPLAY_METRIC_TITLES) {
      await expect(grid.getByRole("heading", { name: title })).toBeVisible();
    }

    await expect(grid.getByText("0.0% landed")).toBeVisible();
    await expect(grid.getByText("0.00x")).toBeVisible();
    await expect(grid.getByText("0 samples")).toHaveCount(7);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });

  test("backend_replay_diff_is_used_without_result_route_injection", async ({
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

    const grid = page.getByTestId("replay-metrics-grid");
    await expect(grid).toBeVisible();
    await expect(grid.getByTestId("replay-chart-block")).toHaveCount(7);
    await expect(page.locator('[data-testid="calibration-band"]')).toHaveCount(0);

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
