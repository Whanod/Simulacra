export interface SyntheticBadgeInput {
  syntheticMode: boolean;
  syntheticMathModel: string | null;
  nonTransferableConclusions: string[];
}

export interface SyntheticBadgeView {
  visible: boolean;
  label: string;
  tooltip: string;
  helpHref: string;
  className: string;
  mathModel: string | null;
}

import { mathModelBadgeLabel } from "@/lib/synthetic/mathModels";

const FALLBACK_TOOLTIP =
  "This template runs synthetic math. Results may not transfer to mainnet.";

export function syntheticBadgeView(
  input: SyntheticBadgeInput | null | undefined,
): SyntheticBadgeView {
  const hidden: SyntheticBadgeView = {
    visible: false,
    label: "",
    tooltip: "",
    helpHref: "/help/synthetic-mode",
    className: "synthetic-badge",
    mathModel: null,
  };
  if (!input || !input.syntheticMode) return hidden;
  const model = input.syntheticMathModel;
  const label = mathModelBadgeLabel(model);
  const firstConclusion = input.nonTransferableConclusions[0];
  const tooltip = firstConclusion && firstConclusion.length > 0
    ? firstConclusion
    : FALLBACK_TOOLTIP;
  return {
    visible: true,
    label,
    tooltip,
    helpHref: "/help/synthetic-mode",
    className: "synthetic-badge",
    mathModel: model ?? null,
  };
}
