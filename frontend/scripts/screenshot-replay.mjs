import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = "http://localhost:3000";
const OUT = "/tmp/defi-sim-screens";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
  deviceScaleFactor: 2,
});
const page = await ctx.newPage();

await page.goto(`${BASE}/replay`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/05-replay-empty.png`, fullPage: false });
console.log("captured 05-replay-empty");

await page.screenshot({ path: `${OUT}/06-replay-empty-full.png`, fullPage: true });
console.log("captured 06-replay-empty-full");

// Try to submit using a fixture-friendly slot
const slotInput = page.getByTestId("replay-slot-search");
await slotInput.fill("160000001");
await page.getByTestId("replay-slot-apply").click();
await page.getByTestId("replay-tip-bundle-id").fill("b-1");
await page.getByTestId("replay-tip-slider").evaluate((el, v) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}, "50000");

const submitBtn = page.getByTestId("replay-submit");
await submitBtn.click();

// Wait for either result or error
await Promise.race([
  page.getByTestId("replay-result").waitFor({ timeout: 20000 }).catch(() => null),
  page.getByTestId("replay-error").waitFor({ timeout: 20000 }).catch(() => null),
]);
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/07-replay-after-submit.png`, fullPage: false });
console.log("captured 07-replay-after-submit");
await page.screenshot({ path: `${OUT}/08-replay-after-submit-full.png`, fullPage: true });
console.log("captured 08-replay-after-submit-full");

await browser.close();
console.log("done");
