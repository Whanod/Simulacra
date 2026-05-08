"use client";

import { useEffect } from "react";
import type { RunSpec } from "@/lib/types/simulations";

export type DataTheme = "solana" | "neutral";

type SpecLike =
  | Pick<RunSpec, "execution">
  | { execution?: { model?: string } | null }
  | null
  | undefined;

export function dataThemeFromSpec(spec: SpecLike): DataTheme {
  const model = spec?.execution?.model;
  return model === "solana" || model === "solana_like" ? "solana" : "neutral";
}

export function useDataTheme(spec: SpecLike): DataTheme {
  const theme = dataThemeFromSpec(spec);
  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  return theme;
}
