import { defineConfig, devices } from "@playwright/test";

const FRONTEND_PORT = Number(process.env.PLAYWRIGHT_FRONTEND_PORT || 3100);
const API_PORT = Number(process.env.PLAYWRIGHT_API_PORT || 8100);
const API_BASE = `http://127.0.0.1:${API_PORT}`;
const FRONTEND_BASE = `http://127.0.0.1:${FRONTEND_PORT}`;

// Privy id can be set under either name. Resolve once so both webServers
// get a consistent value.
const PRIVY_APP_ID =
  process.env.PRIVY_APP_ID || process.env.PRIVY_ID || "";
const NEXT_PUBLIC_PRIVY_APP_ID =
  process.env.NEXT_PUBLIC_PRIVY_APP_ID || PRIVY_APP_ID || "";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: FRONTEND_BASE,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: `./.venv/bin/uvicorn defi_sim_api.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: "..",
      url: `${API_BASE}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        DEFI_SIM_ARTIFACT_ROOT: "/tmp/defi-sim-e2e-artifacts",
        CORS_ALLOWED_ORIGINS: FRONTEND_BASE,
        // Privy (Flow 01 spec) — forwarded only when set so existing
        // specs keep running in open mode.
        ...(PRIVY_APP_ID ? { PRIVY_APP_ID, PRIVY_ID: PRIVY_APP_ID } : {}),
      },
    },
    {
      command: `bun run dev -- --port ${FRONTEND_PORT}`,
      url: FRONTEND_BASE,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        NEXT_PUBLIC_API_URL: API_BASE,
        ...(NEXT_PUBLIC_PRIVY_APP_ID
          ? { NEXT_PUBLIC_PRIVY_APP_ID }
          : {}),
      },
    },
  ],
});
