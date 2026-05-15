import { expect, test } from "@playwright/test";

/**
 * Flow 01 — Gate at entry (privy.md §6).
 *
 * Env-gated: only runs when both PRIVY_APP_ID (backend JWKS check) and
 * NEXT_PUBLIC_PRIVY_APP_ID (frontend SDK) are set in the host
 * environment. The playwright.config forwards them to the spawned
 * uvicorn / next-dev processes. Without these the spec skips, so the
 * default playwright run on a contributor laptop or CI without secrets
 * stays green.
 *
 * The OTP flow itself requires either:
 *  - a Privy sandbox app + a documented test code that always works, or
 *  - a network egress to Privy's auth API + the ability to read the
 *    real OTP from a captured email mailbox.
 *
 * The first three checks below don't need either — they exercise the
 * gate behaviour (modal present / public route bypass / inert shell)
 * which lives entirely in our code. The deeper "actually log in" cases
 * are skipped pending a Privy sandbox decision.
 */

const PRIVY_CONFIGURED =
  Boolean(process.env.PRIVY_APP_ID) && Boolean(process.env.NEXT_PUBLIC_PRIVY_APP_ID);

test.describe("flow 01 — gate at entry", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(
      !PRIVY_CONFIGURED,
      "PRIVY_APP_ID + NEXT_PUBLIC_PRIVY_APP_ID must be set to run Flow 01 e2e",
    );
    void testInfo;
  });

  test("anonymous landing → blocking modal, content underneath inert", async ({ page }) => {
    await page.goto("/");
    // Modal is announced as a dialog.
    const modal = page.getByRole("dialog");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveAttribute("aria-modal", "true");

    // Email input has focus on mount.
    const emailInput = modal.getByLabel(/email/i);
    await expect(emailInput).toBeFocused();

    // The route shell underneath is rendered inert; pointer events on
    // the body should not reach behind-modal links.
    const inertShell = page.locator("[inert]");
    await expect(inertShell).toBeVisible();

    // Escape is a no-op while gated.
    await page.keyboard.press("Escape");
    await expect(modal).toBeVisible();
  });

  test("share-link route /r/[runId] bypasses the gate", async ({ page }) => {
    // Even without an existing run, the page must render its own shell
    // (likely a 404 or "no run" state), not the auth modal — that's the
    // public-route allowlist contract.
    await page.goto("/r/does-not-exist");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("invalid email keeps Continue disabled", async ({ page }) => {
    await page.goto("/");
    const modal = page.getByRole("dialog");
    const emailInput = modal.getByLabel(/email/i);
    const continueBtn = modal.getByRole("button", { name: /continue with email/i });

    await emailInput.fill("not-an-email");
    await expect(continueBtn).toBeDisabled();

    await emailInput.fill("alice@example.com");
    await expect(continueBtn).toBeEnabled();
  });

  // The success-flow tests require a Privy sandbox + a deterministic
  // OTP, neither of which is wired up yet. Track in privy.md §8.
  test.skip("full email-OTP success → /dashboard redirect", () => {});
  test.skip("sign out reappears modal in place (no URL change)", () => {});
  test.skip("two-user run-list isolation", () => {});
});
