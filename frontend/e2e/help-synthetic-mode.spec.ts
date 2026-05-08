import { test, expect, type APIRequestContext } from "@playwright/test";
import { API_BASE } from "./_helpers";

interface ApiTemplate {
  template_id: string;
  name: string;
  synthetic_mode?: boolean;
  synthetic_math_model?: string | null;
  non_transferable_conclusions?: string[];
}

async function fetchSyntheticTemplates(
  request: APIRequestContext,
): Promise<ApiTemplate[]> {
  const res = await request.get(`${API_BASE}/templates/experiments`);
  if (!res.ok()) {
    throw new Error(
      `GET /templates/experiments failed: ${res.status()} ${await res.text()}`,
    );
  }
  const body = (await res.json()) as { templates: ApiTemplate[] };
  return (body.templates ?? []).filter((t) => t.synthetic_mode === true);
}

test.describe("help /synthetic-mode page", () => {
  test("help_page_lists_per_template_caveats", async ({ page, request }) => {
    const templates = await fetchSyntheticTemplates(request);
    expect(templates.length).toBeGreaterThanOrEqual(4);

    await page.goto("/help/synthetic-mode");

    await expect(page.locator('[data-help-page="synthetic-mode"]')).toBeVisible();
    await page.locator("[data-template-id]").first().waitFor();

    for (const tpl of templates) {
      const section = page.locator(`[data-template-id="${tpl.template_id}"]`);
      await expect(section).toBeVisible();
      const conclusions = tpl.non_transferable_conclusions ?? [];
      expect(
        conclusions.length,
        `template ${tpl.template_id} must declare at least one non-transferable conclusion`,
      ).toBeGreaterThan(0);
      for (const c of conclusions) {
        await expect(section).toContainText(c);
      }
    }
  });
});
