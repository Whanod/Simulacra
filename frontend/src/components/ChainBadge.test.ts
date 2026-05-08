import { describe, it, expect } from "vitest";
import { chainBadgeView } from "./chainBadgeView";

describe("chainBadgeView", () => {
  it("renders_solana_label_and_gradient_when_solana", () => {
    const view = chainBadgeView({ execution: { model: "solana_like" } });
    expect(view.label).toBe("Solana");
    expect(view.theme).toBe("solana");
    expect(view.isSolana).toBe(true);
    expect(view.className).toContain("chain-badge");
    expect(view.className).toContain("chain-badge-solana");
  });

  it("renders_neutral_label_for_non_solana_spec", () => {
    const view = chainBadgeView({ execution: { model: "round_based" } });
    expect(view.label).not.toBe("Solana");
    expect(view.theme).toBe("neutral");
    expect(view.isSolana).toBe(false);
    expect(view.className).toContain("chain-badge-neutral");
    expect(view.className).not.toContain("chain-badge-solana");
  });

  it("falls_back_to_neutral_for_missing_spec", () => {
    expect(chainBadgeView(null).theme).toBe("neutral");
    expect(chainBadgeView(undefined).theme).toBe("neutral");
    expect(chainBadgeView({}).theme).toBe("neutral");
  });

  it("derives_native_token_symbol_from_chain_idiom", () => {
    const solana = chainBadgeView({ execution: { model: "solana_like" } });
    expect(solana.nativeTokenSymbol).toBe("SOL");
    const neutral = chainBadgeView({ execution: { model: "round_based" } });
    expect(neutral.nativeTokenSymbol).toBe("TOKEN");
    expect(neutral.label).toBe("TOKEN");
  });
});
