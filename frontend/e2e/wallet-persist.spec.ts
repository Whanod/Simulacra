import { expect, test } from "@playwright/test";
import { API_BASE, seedRun } from "./_helpers";
import {
  installArtifactSigningPhantomMock,
  MOCK_SIGNING_WALLET_OWNER,
  mockDevnetWalletRpc,
} from "./wallet-mocks";

function shortAddress(value: string): string {
  return `${value.slice(0, 4)}...${value.slice(-4)}`;
}

test("wallet_signed_artifact_persists_beyond_30_days", async ({ page, request }) => {
  const runId = await seedRun(request, { seed: 2910, numRounds: 4 });
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await installArtifactSigningPhantomMock(page);
  await mockDevnetWalletRpc(page, MOCK_SIGNING_WALLET_OWNER);

  await page.goto(`/results/${encodeURIComponent(runId)}`);
  await expect(page.getByTestId("wallet-artifact-persistence")).toContainText(
    "Ephemeral link expires",
  );

  await expect(page.getByTestId("wallet-select")).toContainText("Phantom");
  await page.getByTestId("wallet-select").selectOption("Phantom");
  const connectButton = page.getByTestId("wallet-connect-button");
  if (await connectButton.isVisible()) await connectButton.click();

  await expect(page.getByTestId("wallet-connected")).toContainText(
    shortAddress(MOCK_SIGNING_WALLET_OWNER),
  );
  await expect(page.getByTestId("wallet-persist-button")).toBeEnabled();
  await page.getByTestId("wallet-persist-button").click();

  await expect(page.getByTestId("wallet-persist-button")).toHaveText("Saved");
  await expect(page.getByTestId("wallet-artifact-persistence")).toContainText(
    `Permanent artifact owned by ${shortAddress(MOCK_SIGNING_WALLET_OWNER)}.`,
  );
  await expect(page.getByTestId("wallet-artifacts-panel")).toContainText(
    /\d+ permanent/,
  );
  await expect(page.getByTestId("wallet-artifact-list")).toContainText(
    runId.slice(0, 6),
  );

  const shareResp = await request.get(
    `${API_BASE}/share/runs/${encodeURIComponent(runId)}`,
  );
  expect(shareResp.ok()).toBeTruthy();
  const shareBody = (await shareResp.json()) as {
    permanent?: boolean;
    expires_at?: string | null;
    run?: { summary?: Record<string, unknown> };
  };
  expect(shareBody.permanent).toBe(true);
  expect(shareBody.expires_at).toBeNull();
  expect(shareBody.run?.summary?.wallet_owner).toBe(MOCK_SIGNING_WALLET_OWNER);

  expect(
    consoleErrors,
    `unexpected console errors: ${consoleErrors.join(" | ")}`,
  ).toEqual([]);
});
