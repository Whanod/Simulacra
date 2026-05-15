import { describe, it, expect } from "vitest";

import { isPublicRoute } from "./publicRoutes";

describe("isPublicRoute", () => {
  it("matches the share-link short-link path", () => {
    expect(isPublicRoute("/r/abc123")).toBe(true);
    expect(isPublicRoute("/r/abc123/")).toBe(false); // bare path only
  });

  it("matches any /embed/ subpath", () => {
    expect(isPublicRoute("/embed/")).toBe(true);
    expect(isPublicRoute("/embed/runs/abc123")).toBe(true);
  });

  it("does not match the gated dashboard path", () => {
    expect(isPublicRoute("/dashboard")).toBe(false);
    expect(isPublicRoute("/runs/abc123")).toBe(false);
  });

  it("does not match a near-miss like /rabbit", () => {
    expect(isPublicRoute("/rabbit")).toBe(false);
  });
});
