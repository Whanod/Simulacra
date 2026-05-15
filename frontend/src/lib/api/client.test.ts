import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, setAuthTokenAccessor } from "@/lib/api/client";

const originalFetch = globalThis.fetch;

function mockOk(body: unknown = {}): typeof fetch {
  return vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  ) as unknown as typeof fetch;
}

function lastRequestHeaders(fn: ReturnType<typeof mockOk>): Headers {
  // The mock is called with (url, init) — pull init.headers off the
  // last invocation. RequestInit's headers may be a record, Headers, or
  // an iterable; normalise to Headers for assertion.
  const calls = (fn as unknown as { mock: { calls: Array<[string, RequestInit]> } }).mock.calls;
  const init = calls[calls.length - 1][1];
  return new Headers(init.headers ?? {});
}

describe("apiFetch auth token attachment", () => {
  beforeEach(() => {
    setAuthTokenAccessor(null);
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setAuthTokenAccessor(null);
  });

  it("omits Authorization when no accessor is registered", async () => {
    const fetchMock = mockOk({ ok: true });
    globalThis.fetch = fetchMock;

    await apiFetch("/runs");

    expect(lastRequestHeaders(fetchMock).get("authorization")).toBeNull();
  });

  it("attaches Bearer header when the accessor returns a token", async () => {
    const fetchMock = mockOk({ ok: true });
    globalThis.fetch = fetchMock;
    setAuthTokenAccessor(async () => "test-jwt");

    await apiFetch("/runs");

    expect(lastRequestHeaders(fetchMock).get("authorization")).toBe("Bearer test-jwt");
  });

  it("falls back to anonymous when the accessor returns null", async () => {
    const fetchMock = mockOk({ ok: true });
    globalThis.fetch = fetchMock;
    setAuthTokenAccessor(async () => null);

    await apiFetch("/runs");

    expect(lastRequestHeaders(fetchMock).get("authorization")).toBeNull();
  });

  it("falls back to anonymous when the accessor throws (no Privy session)", async () => {
    const fetchMock = mockOk({ ok: true });
    globalThis.fetch = fetchMock;
    setAuthTokenAccessor(async () => {
      throw new Error("no session");
    });

    await apiFetch("/runs");

    expect(lastRequestHeaders(fetchMock).get("authorization")).toBeNull();
  });

  it("lets caller-supplied Authorization override the accessor", async () => {
    const fetchMock = mockOk({ ok: true });
    globalThis.fetch = fetchMock;
    setAuthTokenAccessor(async () => "from-accessor");

    await apiFetch("/runs", { headers: { Authorization: "Bearer override" } });

    expect(lastRequestHeaders(fetchMock).get("authorization")).toBe("Bearer override");
  });
});
