import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const OUT = "/tmp/defi-sim-screens";

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});
const page = await ctx.newPage();

async function shot(path, file) {
  await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/${file}.png`, fullPage: false });
  console.log(`captured ${file}`);
}

await page.evaluate(() => {});
await import("node:fs").then((fs) => fs.mkdirSync(OUT, { recursive: true }));

await shot("/dashboard", "01-dashboard");

// Open the +New dropdown
await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await page.locator('button:has-text("+ New")').first().click();
await page.waitForTimeout(300);
await page.screenshot({ path: `${OUT}/02-new-dropdown.png`, fullPage: false });
console.log("captured 02-new-dropdown");

// Replay page to see sidebar with primary verb active
await shot("/replay", "03-replay");

// Calibration page to see Reference muted state when active
await shot("/calibration", "04-calibration");

await browser.close();
console.log("done");
