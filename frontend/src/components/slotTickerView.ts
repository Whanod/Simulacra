import { dataThemeFromSpec } from "@/lib/hooks/useDataTheme";
import type { RunSpec } from "@/lib/types/simulations";

export type SlotTickerSpec =
  | Pick<RunSpec, "execution">
  | { execution?: { model?: string } | null }
  | null
  | undefined;

export interface SlotTickerView {
  shouldRender: boolean;
  label: string;
  title: string;
  dataLiveChrome: "placeholder" | "live";
}

export function slotTickerView(
  spec: SlotTickerSpec,
  liveSlot: number,
  liveLeader: string | null = null,
): SlotTickerView {
  const shouldRender = dataThemeFromSpec(spec) === "solana";
  const isLive = liveSlot > 0;
  const baseLabel = `Slot ${liveSlot}`;
  const label =
    isLive && liveLeader ? `${baseLabel} · ${liveLeader.slice(0, 8)}` : baseLabel;
  const title = isLive
    ? liveLeader
      ? `Slot ${liveSlot} · leader ${liveLeader}`
      : `Slot ${liveSlot}`
    : "Live slot (awaiting first snapshot)";
  return {
    shouldRender,
    label,
    title,
    dataLiveChrome: isLive ? "live" : "placeholder",
  };
}
