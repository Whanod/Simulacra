import { MATH_MODELS, type MathModelInfo } from "@/lib/synthetic/mathModels";
import type { SimTemplate } from "@/lib/api/adapters/templates";

export interface TemplateCaveatSection {
  id: string;
  name: string;
  mathModelDisplayName: string;
  conclusions: string[];
}

export interface MathModelSection {
  id: string;
  displayName: string;
  invariantPlain: string;
}

export interface SyntheticHelpView {
  mathModelSections: MathModelSection[];
  templateSections: TemplateCaveatSection[];
}

const PHASE_0_MODEL_IDS = ["l2_norm_cfamm", "clob"];

function mathModelDisplayName(model: string | null): string {
  if (!model) return "Unspecified";
  const info = MATH_MODELS[model];
  return info ? info.displayName : model;
}

export function syntheticHelpView(
  templates: SimTemplate[] | null | undefined,
): SyntheticHelpView {
  const list = Array.isArray(templates) ? templates : [];
  const synthetic = list.filter((t) => t.syntheticMode);

  const usedModelIds = new Set<string>();
  for (const t of synthetic) {
    if (t.syntheticMathModel) usedModelIds.add(t.syntheticMathModel);
  }
  for (const id of PHASE_0_MODEL_IDS) usedModelIds.add(id);

  const mathModelSections: MathModelSection[] = [];
  for (const id of usedModelIds) {
    const info: MathModelInfo | undefined = MATH_MODELS[id];
    if (!info) continue;
    mathModelSections.push({
      id: info.id,
      displayName: info.displayName,
      invariantPlain: info.invariantPlain,
    });
  }

  const templateSections: TemplateCaveatSection[] = synthetic.map((t) => ({
    id: t.id,
    name: t.name,
    mathModelDisplayName: mathModelDisplayName(t.syntheticMathModel),
    conclusions: t.nonTransferableConclusions,
  }));

  return { mathModelSections, templateSections };
}
