import { describe, it, expect } from "vitest";
import { slotTickerView } from "./slotTickerView";

describe("slotTickerView", () => {
  it("renders_placeholder_when_no_slot_observed", () => {
    const view = slotTickerView({ execution: { model: "solana_like" } }, 0);
    expect(view.shouldRender).toBe(true);
    expect(view.label).toBe("Slot 0");
    expect(view.dataLiveChrome).toBe("placeholder");
  });

  it("renders_live_chrome_when_slot_advances", () => {
    const view = slotTickerView({ execution: { model: "solana_like" } }, 7);
    expect(view.shouldRender).toBe(true);
    expect(view.label).toBe("Slot 7");
    expect(view.dataLiveChrome).toBe("live");
  });

  it("includes_truncated_leader_in_label_when_present", () => {
    const view = slotTickerView(
      { execution: { model: "solana_like" } },
      12345,
      "ValidatorPubkey1234567890",
    );
    expect(view.label).toBe("Slot 12345 · Validato");
    expect(view.dataLiveChrome).toBe("live");
    expect(view.title).toContain("ValidatorPubkey1234567890");
  });

  it("hides_when_spec_is_neutral", () => {
    const view = slotTickerView({ execution: { model: "round_based" } }, 42);
    expect(view.shouldRender).toBe(false);
  });

  it("hides_when_spec_missing", () => {
    expect(slotTickerView(null, 0).shouldRender).toBe(false);
    expect(slotTickerView(undefined, 0).shouldRender).toBe(false);
    expect(slotTickerView({}, 0).shouldRender).toBe(false);
  });
});
