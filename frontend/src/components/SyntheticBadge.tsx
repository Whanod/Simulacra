"use client";

import Link from "next/link";
import {
  syntheticBadgeView,
  type SyntheticBadgeInput,
} from "./syntheticBadgeView";

export { syntheticBadgeView };
export type { SyntheticBadgeInput, SyntheticBadgeView } from "./syntheticBadgeView";

interface SyntheticBadgeProps {
  template: SyntheticBadgeInput | null | undefined;
}

export default function SyntheticBadge({ template }: SyntheticBadgeProps) {
  const view = syntheticBadgeView(template);
  if (!view.visible) return null;
  return (
    <Link
      href={view.helpHref}
      className={view.className}
      data-synthetic-badge="true"
      data-math-model={view.mathModel ?? undefined}
      title={view.tooltip}
    >
      {view.label}
    </Link>
  );
}
