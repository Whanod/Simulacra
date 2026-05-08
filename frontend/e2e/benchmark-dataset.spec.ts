import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

interface CorpusSlot {
  slot: number;
  run_count: number;
}

interface CorpusResponse {
  slots: CorpusSlot[];
}

async function firstCorpusSlot(request: APIRequestContext) {
  const response = await request.get(`${API_BASE}/v1/calibration/corpus`);
  if (!response.ok()) {
    throw new Error(`corpus lookup failed: ${response.status()} ${await response.text()}`);
  }
  const body = (await response.json()) as CorpusResponse;
  const slot = body.slots[0];
  if (!slot) throw new Error("calibration corpus has no committed slots");
  return slot;
}

test("benchmark_runnable_updates_scoreboard", async ({ page, request }) => {
  const slot = await firstCorpusSlot(request);
  const expectedRunCount = slot.run_count + 1;
  const consoleErrors: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto(`/benchmark/${slot.slot}`);

  await expect(page.getByTestId("benchmark-page")).toBeVisible();
  await expect(page.getByTestId("benchmark-slot")).toHaveText(`Slot ${slot.slot}`);
  await expect(page.getByTestId("benchmark-run-count")).toHaveText(
    String(slot.run_count),
  );
  await expect(page.getByTestId("benchmark-scoreboard")).toBeVisible();
  await expect(page.getByTestId("benchmark-scoreboard-row").first()).toBeVisible();

  const replayResponsePromise = page.waitForResponse(
    (response) =>
      response.url() === `${API_BASE}/v1/replay` &&
      response.request().method() === "POST",
  );
  await page.getByTestId("benchmark-run-button").click();
  const replayResponse = await replayResponsePromise;
  expect(replayResponse.ok(), await replayResponse.text()).toBe(true);

  const replayBody = (await replayResponse.json()) as { run_id: string };
  await expect(page.getByTestId("benchmark-run-id")).toHaveText(replayBody.run_id);
  await expect(page.getByTestId("benchmark-run-count")).toHaveText(
    String(expectedRunCount),
  );
  await expect(page.getByTestId("benchmark-last-run")).not.toHaveText(
    "No completed benchmark run",
  );
  await expect(page.getByTestId("benchmark-scoreboard")).toBeVisible();

  expect(
    consoleErrors,
    `unexpected console errors: ${consoleErrors.join(" | ")}`,
  ).toEqual([]);
});
