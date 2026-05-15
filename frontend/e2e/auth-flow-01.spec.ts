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
  Boolean(process.env.PRIVY_APP_ID || process.env.PRIVY_ID) &&
  Boolean(process.env.NEXT_PUBLIC_PRIVY_APP_ID || process.env.PRIVY_APP_ID || process.env.PRIVY_ID);

test.describe("flow 01 — gate at entry", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(
      !PRIVY_CONFIGURED,
      "PRIVY_APP_ID + NEXT_PUBLIC_PRIVY_APP_ID must be set to run Flow 01 e2e",
    );
    void testInfo;
  });

  test("anonymous landing → full-page sign-in screen, studio not mounted", async ({ page }) => {
    await page.goto("/");
    // The sign-in screen replaces the studio entirely (no overlay,
    // no broken shell behind it).
    const signin = page.getByTestId("auth-signin-screen");
    await expect(signin).toBeVisible();

    // Email input has focus on mount.
    const emailInput = signin.getByLabel(/email/i);
    await expect(emailInput).toBeFocused();

    // The studio shell is *not* in the DOM at all — the gate replaces
    // it rather than overlaying. (The Sidebar component lives inside
    // the (studio) layout group and only mounts post-auth.)
    await expect(page.locator("#main")).toHaveCount(0);

    // Escape inside the card is a no-op (no modal to dismiss anyway,
    // but we still swallow the keystroke for symmetry with the prior
    // contract).
    await page.keyboard.press("Escape");
    await expect(signin).toBeVisible();
  });

  test("share-link route /r/[runId] bypasses the gate", async ({ page }) => {
    // Even without an existing run, the page must render its own shell
    // (likely a 404 or "no run" state), not the sign-in screen — that's
    // the public-route allowlist contract.
    await page.goto("/r/does-not-exist");
    await expect(page.getByTestId("auth-signin-screen")).toHaveCount(0);
  });

  test("invalid email keeps Continue disabled", async ({ page }) => {
    await page.goto("/");
    const screen = page.getByTestId("auth-signin-screen");
    const emailInput = screen.getByLabel(/email/i);
    const continueBtn = screen.getByRole("button", { name: /continue with email/i });

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
