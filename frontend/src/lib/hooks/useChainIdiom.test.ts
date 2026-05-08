import { describe, expect, it } from "vitest";
import { useChainIdiom } from "./useChainIdiom";

describe("useChainIdiom", () => {
  it("returns_solana_idiom_for_solana_like", () => {
    const idiom = useChainIdiom({ execution: { model: "solana_like" } });
    expect(idiom.time_unit).toBe("slot");
    expect(idiom.time_label).toBe("Slot time");
    expect(idiom.fee_label).toBe("Compute & priority fees");
    expect(idiom.epoch_label).toBe("Epoch (slots)");
    expect(idiom.time_default).toBe(0.4);
    expect(idiom.epoch_default).toBe(432_000);
    expect(idiom.native_token_symbol).toBe("SOL");
    expect(idiom.native_token_decimals).toBe(9);
  });

  it("falls_back_to_neutral_for_unknown", () => {
    const unknown = useChainIdiom({ execution: { model: "something-else" } });
    expect(unknown.time_unit).toBe("round");
    expect(unknown.fee_label).not.toBe("Compute & priority fees");
    expect(unknown.epoch_label).not.toBe("Epoch (slots)");

    const missing = useChainIdiom(undefined);
    expect(missing.time_unit).toBe("round");

    const empty = useChainIdiom({ execution: {} });
    expect(empty.time_unit).toBe("round");
    expect(empty.epoch_default).toBe(1);
  });
});
