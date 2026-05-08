import { chainIdiomFromSpec, type ChainIdiom } from "@/lib/hooks/useChainIdiom";
import { dataThemeFromSpec, type DataTheme } from "@/lib/hooks/useDataTheme";
import type { RunSpec } from "@/lib/types/simulations";

export type ChainBadgeSpec =
  | Pick<RunSpec, "execution">
  | { execution?: { model?: string } | null }
  | null
  | undefined;

export interface ChainBadgeView {
  label: string;
  theme: DataTheme;
  className: string;
  isSolana: boolean;
  nativeTokenSymbol: string;
}

export function chainBadgeViewFromIdiom(
  spec: ChainBadgeSpec,
  idiom: ChainIdiom,
): ChainBadgeView {
  const theme = dataThemeFromSpec(spec);
  const isSolana = theme === "solana";
  const nativeTokenSymbol = idiom.native_token_symbol;
  const label = isSolana ? "Solana" : nativeTokenSymbol;
  const className = isSolana
    ? "chain-badge chain-badge-solana"
    : "chain-badge chain-badge-neutral";
  return { label, theme, className, isSolana, nativeTokenSymbol };
}

export function chainBadgeView(spec: ChainBadgeSpec): ChainBadgeView {
  return chainBadgeViewFromIdiom(spec, chainIdiomFromSpec(spec));
}
