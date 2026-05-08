import { describe, it, expect } from "vitest";
import { fromApiEvent, fromApiEvents } from "@/lib/api/adapters/events";

const ALLOWED_CLASSES = new Set(["trade", "lp", "oracle", "reward", "fail"]);

describe("events adapter (re-export from runs)", () => {
  it("fromApiEvents maps a list", () => {
    const out = fromApiEvents([
      { type: "SIMULATION_START", round: 0 },
      { type: "ACTION_EXECUTED", round: 1, data: { agent_id: "a", action: "swap" } },
    ]);
    expect(out).toHaveLength(2);
    expect(out[0].evType).toBe("SIMULATION_START");
    // US-015: class is hash-derived; assert the bucket only.
    expect(ALLOWED_CLASSES.has(out[1].cls)).toBe(true);
    expect(out[1].detail).toContain("a");
  });

  it("fromApiEvent defaults round to 0", () => {
    expect(fromApiEvent({ type: "ACTION_EXECUTED" }).round).toBe(0);
  });
});
