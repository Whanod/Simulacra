import { describe, it, expect } from "vitest";

describe("api harness smoke", () => {
  it("reaches /health on the spawned backend", async () => {
    const base = process.env.DEFI_SIM_INT_API_URL || process.env.NEXT_PUBLIC_API_URL;
    expect(base, "global setup must set NEXT_PUBLIC_API_URL").toBeTruthy();
    const res = await fetch(`${base}/health`);
    expect(res.ok).toBe(true);
    const json = (await res.json()) as { status?: string };
    expect(json.status).toBe("ok");
  });

  it("apiFetch wrapper reaches /health", async () => {
    const { apiFetch } = await import("@/lib/api/client");
    const json = await apiFetch<{ status: string }>("/health");
    expect(json.status).toBe("ok");
  });
});
