import { expect, test } from "@playwright/test";
import {
  installPhantomMock,
  mockDevnetWalletRpc,
} from "./wallet-mocks";

test.describe("wallet connect", () => {
  test("connects_phantom_mock_and_shows_positions", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await installPhantomMock(page);
    await mockDevnetWalletRpc(page);

    await page.goto("/dashboard");
    await expect(page.getByTestId("wallet-positions-panel")).toContainText(
      "Connect to load accounts.",
    );

    await expect(page.getByTestId("wallet-select")).toContainText("Phantom");
    await page.getByTestId("wallet-select").selectOption("Phantom");
    const connectButton = page.getByTestId("wallet-connect-button");
    if (await connectButton.isVisible()) await connectButton.click();

    await expect(page.getByTestId("wallet-connected")).toContainText("2EPt...huZV");
    await expect(page.getByTestId("wallet-position-summary")).toContainText("3 accounts");
    await expect(page.getByTestId("wallet-position-summary")).toContainText(
      "1 LP candidates",
    );
    await expect(page.getByTestId("wallet-position-list")).toContainText("2.5 SOL");
    await expect(page.getByTestId("wallet-position-list")).toContainText(
      "Position NFT candidate",
    );
    await expect(page.getByTestId("wallet-position-list")).toContainText("1.5");

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
