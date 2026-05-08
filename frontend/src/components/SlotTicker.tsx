"use client";

import { useStudioStore } from "@/lib/state/useStudioStore";
import { slotTickerView, type SlotTickerSpec } from "./slotTickerView";

export { slotTickerView };
export type { SlotTickerSpec, SlotTickerView } from "./slotTickerView";

interface SlotTickerProps {
  spec?: SlotTickerSpec;
}

export default function SlotTicker({ spec }: SlotTickerProps) {
  const { liveSlot, liveLeader } = useStudioStore();
  const view = slotTickerView(spec ?? null, liveSlot, liveLeader);
  if (!view.shouldRender) return null;
  return (
    <span
      className="slot-ticker"
      data-live-chrome={view.dataLiveChrome}
      title={view.title}
    >
      {view.label}
    </span>
  );
}
