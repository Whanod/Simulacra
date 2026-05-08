"use client";

import { useChainIdiom } from "@/lib/hooks/useChainIdiom";
import {
  chainBadgeView,
  chainBadgeViewFromIdiom,
  type ChainBadgeSpec,
} from "./chainBadgeView";

export { chainBadgeView };
export type { ChainBadgeSpec, ChainBadgeView } from "./chainBadgeView";

interface ChainBadgeProps {
  spec?: ChainBadgeSpec;
}

export default function ChainBadge({ spec }: ChainBadgeProps) {
  const idiom = useChainIdiom(spec ?? null);
  const view = chainBadgeViewFromIdiom(spec ?? null, idiom);
  return (
    <span
      className={view.className}
      data-chain-badge={view.theme}
      data-native-token={view.nativeTokenSymbol}
      title={view.isSolana ? "Solana execution" : "Neutral execution"}
    >
      {view.label}
    </span>
  );
}
