import { test, expect } from "@playwright/test";
import { API_BASE, FRONTEND_BASE, seedRun } from "./_helpers";

test("iframe_embed_renders_chart", async ({ page, request }) => {
  const runId = await seedRun(request, { seed: 2909, numRounds: 5 });
  const embedUrl = `${API_BASE}/embed/cumulative-volume?run=${encodeURIComponent(runId)}`;
  const consoleErrors: string[] = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto(FRONTEND_BASE);
  await page.setContent(`
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>Third-party embed host</title>
        <style>
          body { margin: 0; background: #f5f7fb; font-family: sans-serif; }
          iframe { display: block; width: 760px; height: 520px; border: 0; }
        </style>
      </head>
      <body>
        <iframe title="Cumulative Volume embed" src="${embedUrl}"></iframe>
      </body>
    </html>
  `);

  const embed = page.frameLocator('iframe[title="Cumulative Volume embed"]');
  await expect(
    embed.locator(`main[data-run-id="${runId}"][data-chart-id="cumulative-volume"]`),
  ).toBeVisible();
  await expect(embed.getByRole("heading", { name: "Cumulative Volume" })).toBeVisible();
  await expect(embed.locator('svg[role="img"]')).toBeVisible();
  await expect(embed.locator("script")).toHaveCount(0);
  await expect(embed.getByRole("link", { name: "Open run" })).toHaveAttribute(
    "href",
    `/r/${runId}`,
  );
  expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
});
