import { test, expect, type APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

interface ApiTemplate {
  template_id: string;
  name: string;
  synthetic_mode?: boolean;
  synthetic_math_model?: string | null;
  non_transferable_conclusions?: string[];
}

const FOUR_TEMPLATE_IDS = [
  "whirlpool-fee-tuning",
  "solana-sandwich-stress",
  "dlmm-bin-sustainability",
  "raydium-vs-whirlpool-arb",
];

async function fetchTemplates(request: APIRequestContext): Promise<ApiTemplate[]> {
  const res = await request.get(`${API_BASE}/templates/experiments`);
  if (!res.ok()) {
    throw new Error(
      `GET /templates/experiments failed: ${res.status()} ${await res.text()}`,
    );
  }
  const body = (await res.json()) as { templates: ApiTemplate[] };
  return body.templates ?? [];
}

test.describe("templates synthetic badge", () => {
  test("synthetic_badge_names_math_model", async ({ page, request }) => {
    const apiTemplates = await fetchTemplates(request);
    const focused = FOUR_TEMPLATE_IDS.map((id) => {
      const t = apiTemplates.find((x) => x.template_id === id);
      if (!t) throw new Error(`Template ${id} missing from API response`);
      return t;
    });

    await page.goto("/builder");
    await page.locator(".card h3").first().waitFor();

    for (const tpl of focused) {
      const card = page.locator(".grid-4 > .card").filter({
        has: page.getByRole("heading", { level: 3, name: tpl.name, exact: true }),
      });
      const badge = card.locator('[data-synthetic-badge="true"]');
      await expect(badge).toBeVisible();
      // Badge must name the math model — "L2-norm CFAMM" — not just "synthetic".
      await expect(badge).toContainText("L2-norm CFAMM");
      await expect(badge).not.toHaveText(/^Synthetic math$/i);
    }
  });

  test("badge_tooltip_shows_non_transferable_summary", async ({
    page,
    request,
  }) => {
    const apiTemplates = await fetchTemplates(request);
    const focused = FOUR_TEMPLATE_IDS.map((id) => {
      const t = apiTemplates.find((x) => x.template_id === id);
      if (!t) throw new Error(`Template ${id} missing from API response`);
      return t;
    });

    await page.goto("/builder");
    await page.locator(".card h3").first().waitFor();

    for (const tpl of focused) {
      const firstConclusion = (tpl.non_transferable_conclusions ?? [])[0];
      expect(
        firstConclusion,
        `template ${tpl.template_id} must declare a non-empty first non_transferable_conclusion`,
      ).toBeTruthy();
      const card = page.locator(".grid-4 > .card").filter({
        has: page.getByRole("heading", { level: 3, name: tpl.name, exact: true }),
      });
      const badge = card.locator('[data-synthetic-badge="true"]');
      await expect(badge).toBeVisible();
      const tooltip = await badge.getAttribute("title");
      expect(tooltip).toBe(firstConclusion);
    }
  });
});
