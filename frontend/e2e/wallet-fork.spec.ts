import { expect, test } from "@playwright/test";
import { API_BASE } from "./_helpers";
import {
  installPhantomMock,
  MOCK_WALLET_OWNER,
  mockDevnetWalletRpc,
} from "./wallet-mocks";

interface BundleSimulatorRequest {
  context_slot: "latest" | number;
  fork_spec?: {
    slot: number;
    protocols: unknown[];
    include_wallet_accounts?: string[] | null;
  } | null;
}

function bundleResponse() {
  return {
    expected_tip_to_land_lamports: 100000,
    landing_probability: 0.99,
    profit_distribution: {
      p50: 12000,
      p90: 25000,
    },
    alt_compression: {
      uncompressed_bytes: 640,
      compressed_bytes: 480,
      used_alts: [],
    },
    cu_budget: {
      tx_cu_used: [42000],
      slot_cu_headroom: 48000000,
      slot_full: false,
    },
    write_lock_contention: {
      blocking_pubkeys: [],
      contended_lock_count: 0,
      relaxed_lock_count: 0,
    },
    tip_optimizer: {
      target_percentile: 90,
      minimum_tip_lamports: 100000,
      safety_margin_lamports: 10000,
      priority_fee_quote_lamports: 0,
    },
    calibration: null,
    metrics: {
      replay: {
        bundle_landing_rate: {
          value: 0.99,
          unit: "probability",
          sample_size: 1,
        },
        tip_efficiency: {
          value: 0.88,
          unit: "ratio",
          sample_size: 1,
        },
        slot_inclusion_latency: {
          value: 1,
          unit: "slots",
          sample_size: 1,
          mean: 1,
          median: 1,
          p95: 1,
          p99: 1,
          samples: [1],
        },
      },
    },
  };
}

test.describe("wallet fork", () => {
  test("fork_with_my_positions_includes_wallet_pubkey", async ({ page }) => {
    const consoleErrors: string[] = [];
    const submittedRequest: { value: BundleSimulatorRequest | null } = { value: null };

    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await installPhantomMock(page);
    await mockDevnetWalletRpc(page);
    await page.route(`${API_BASE}/v1/simulate-bundle`, async (route) => {
      submittedRequest.value = route.request().postDataJSON() as BundleSimulatorRequest;
      await route.fulfill({
        contentType: "application/json",
        json: bundleResponse(),
      });
    });

    await page.goto("/dashboard");
    await expect(page.getByTestId("wallet-select")).toContainText("Phantom");
    await page.getByTestId("wallet-select").selectOption("Phantom");
    const connectButton = page.getByTestId("wallet-connect-button");
    if (await connectButton.isVisible()) await connectButton.click();

    await expect(page.getByTestId("wallet-connected")).toContainText("2EPt...huZV");
    await page.getByTestId("wallet-open-fork-button").click();
    await expect(page).toHaveURL(/\/bundle-simulator$/);

    await expect(page.getByTestId("wallet-fork-control")).toContainText(
      "2EPt...huZV",
    );
    await page.getByTestId("wallet-fork-button").click();
    await expect(page.getByTestId("wallet-fork-spec-preview")).toContainText(
      MOCK_WALLET_OWNER,
    );
    await expect(page.getByTestId("wallet-fork-spec-preview")).toContainText(
      '"include_wallet_accounts"',
    );

    const responsePromise = page.waitForResponse(
      (response) =>
        response.url() === `${API_BASE}/v1/simulate-bundle` &&
        response.request().method() === "POST",
    );
    await page.getByTestId("bundle-run-button").click();
    const response = await responsePromise;
    expect(response.ok()).toBe(true);

    expect(submittedRequest.value?.context_slot).toBe(250000000);
    expect(submittedRequest.value?.fork_spec).toEqual({
      slot: 250000000,
      protocols: [],
      include_wallet_accounts: [MOCK_WALLET_OWNER],
    });
    await expect(page.getByTestId("bundle-result-panel")).toBeVisible();

    expect(
      consoleErrors,
      `unexpected console errors: ${consoleErrors.join(" | ")}`,
    ).toEqual([]);
  });
});
