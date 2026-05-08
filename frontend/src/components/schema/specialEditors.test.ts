import { afterEach, describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import {
  getSpecialEditor,
  listRegisteredSpecialEditors,
  registerSpecialEditor,
  unregisterSpecialEditor,
  type SpecialEditorProps,
} from "./specialEditors";

// A throwaway plugin used across tests. The body never runs — the
// tests only exercise registry bookkeeping, not render output.
const FakePlugin: ComponentType<SpecialEditorProps> = () => null;
const AnotherPlugin: ComponentType<SpecialEditorProps> = () => null;

describe("special editor registry (US-010)", () => {
  afterEach(() => {
    unregisterSpecialEditor("test-fixture");
    unregisterSpecialEditor("test-other");
  });

  it("returns undefined when no plugin is registered for a key", () => {
    expect(getSpecialEditor("world-markets-graph")).toBeUndefined();
    expect(getSpecialEditor(undefined)).toBeUndefined();
    expect(getSpecialEditor("")).toBeUndefined();
  });

  it("returns the registered plugin for its key", () => {
    registerSpecialEditor("test-fixture", FakePlugin);
    expect(getSpecialEditor("test-fixture")).toBe(FakePlugin);
  });

  it("overwrites a previously registered plugin for the same key", () => {
    registerSpecialEditor("test-fixture", FakePlugin);
    registerSpecialEditor("test-fixture", AnotherPlugin);
    expect(getSpecialEditor("test-fixture")).toBe(AnotherPlugin);
  });

  it("lists registered keys in sorted order", () => {
    registerSpecialEditor("test-other", FakePlugin);
    registerSpecialEditor("test-fixture", FakePlugin);
    const keys = listRegisteredSpecialEditors();
    expect(keys).toContain("test-fixture");
    expect(keys).toContain("test-other");
    expect([...keys].sort()).toEqual(keys);
  });

  it("unregisters cleanly", () => {
    registerSpecialEditor("test-fixture", FakePlugin);
    expect(getSpecialEditor("test-fixture")).toBe(FakePlugin);
    unregisterSpecialEditor("test-fixture");
    expect(getSpecialEditor("test-fixture")).toBeUndefined();
  });
});

// The `noop-preview` fixture plugin is verified end-to-end by the
// playwright spec (`e2e/schema-preview.spec.ts`) — the preview page
// side-imports `registerSpecialEditors.ts` which wires it in. We do
// not assert that in vitest because `NoopSpecialEditor.tsx` uses
// JSX and this project's vitest config has no React transform.
