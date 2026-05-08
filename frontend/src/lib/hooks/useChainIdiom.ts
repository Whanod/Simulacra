import type { RunSpec } from "@/lib/types/simulations";

export interface ChainIdiom {
  time_unit: "slot" | "round";
  time_label: string;
  time_default: number;
  epoch_default: number;
  fee_label: string;
  epoch_label: string;
  round_label: string;
  rounds_label: string;
  native_token_symbol: string;
  native_token_decimals: number;
}

export const SOLANA_IDIOM: ChainIdiom = {
  time_unit: "slot",
  time_label: "Slot time",
  time_default: 0.4,
  epoch_default: 432_000,
  fee_label: "Compute & priority fees",
  epoch_label: "Epoch (slots)",
  round_label: "Slot",
  rounds_label: "Slots",
  native_token_symbol: "SOL",
  native_token_decimals: 9,
};

export const NEUTRAL_IDIOM: ChainIdiom = {
  time_unit: "round",
  time_label: "Round time",
  time_default: 12,
  epoch_default: 1,
  fee_label: "Fee model",
  epoch_label: "Epoch length",
  round_label: "Round",
  rounds_label: "Rounds",
  native_token_symbol: "TOKEN",
  native_token_decimals: 18,
};

type SpecLike =
  | Pick<RunSpec, "execution">
  | { execution?: { model?: string } | null }
  | null
  | undefined;

export function chainIdiomFromSpec(spec: SpecLike): ChainIdiom {
  const model = spec?.execution?.model;
  if (model === "solana" || model === "solana_like") return SOLANA_IDIOM;
  return NEUTRAL_IDIOM;
}

export function useChainIdiom(spec: SpecLike): ChainIdiom {
  return chainIdiomFromSpec(spec);
}
