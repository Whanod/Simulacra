import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  oxc: {
    jsx: {
      runtime: "automatic",
      importSource: "react",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    include: [
      "src/**/*.test.ts",
      "src/**/*.test.tsx",
      "test/integration/**/*.int.test.ts",
    ],
    exclude: ["node_modules", ".next", "e2e"],
    globalSetup: ["./test/integration/setup.ts"],
    testTimeout: 30_000,
    hookTimeout: 60_000,
    passWithNoTests: true,
  },
});
