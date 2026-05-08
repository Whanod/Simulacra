// PRD US-004 line 781: studio overlay shape for the calibration band shown
// on every chart in `frontend/src/app/(studio)/results/[runId]/page.tsx`.
//
// Pure-logic module: takes a per-metric ErrorBand-shaped payload + the
// configured threshold, returns a computed view the React component renders
// without further branching. Mirrors `syntheticBadgeView` in style.

export interface CalibrationThreshold {
  /** Either `relative` (fraction in [0,1]) or `absolute` (count). One must be set. */
  relative?: number | null;
  absolute?: number | null;
}

export interface CalibrationBandInput {
  /** Whether the underlying run carries a `mainnet_accuracy_claim`. */
  isCalibratedReplay: boolean;
  /** Predicted (model) value for this chart's metric. */
  predicted: number | null | undefined;
  /** Mainnet-actual value for this chart's metric (null when decoder absent). */
  actual: number | null | undefined;
  /** Whether the actual side was extractable. False == decoder not landed. */
  supported: boolean;
  /** Per-metric threshold from `solana-plans/calibration/thresholds.yaml`. */
  threshold: CalibrationThreshold | null | undefined;
  /** Decimal places to render predicted/mainnet/delta with. */
  fractionDigits?: number;
}

export interface CalibrationBandView {
  visible: boolean;
  /** "Model: 2.41" */
  modelText: string;
  /** "Mainnet: 2.39" or "Mainnet: —" when unsupported. */
  mainnetText: string;
  /** "Δ = +0.02" or "—" when unsupported. */
  deltaText: string;
  /** "(0.84%)" — relative error, or empty string when not computable. */
  relErrorText: string;
  /** "within 0.5% threshold" / "out-of-band 0.5% threshold" / "decoder pending" / "no threshold". */
  thresholdText: string;
  /** Rendered marker: "✓" / "✗" / "⏳" (decoder pending) / "" when no threshold. */
  marker: string;
  /** True iff supported && threshold set && error within threshold. */
  withinThreshold: boolean;
  /** True iff supported && threshold set && error breached. */
  breached: boolean;
  /** Single-line composed string per PRD line 784. */
  composed: string;
}

const HIDDEN: CalibrationBandView = {
  visible: false,
  modelText: "",
  mainnetText: "",
  deltaText: "",
  relErrorText: "",
  thresholdText: "",
  marker: "",
  withinThreshold: false,
  breached: false,
  composed: "",
};

function formatNumber(n: number, digits: number): string {
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function formatSigned(n: number, digits: number): string {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "" : "";
  return `${sign}${n.toFixed(digits)}`;
}

function formatThresholdLabel(t: CalibrationThreshold): string {
  if (typeof t.relative === "number") return `${(t.relative * 100).toFixed(2)}%`;
  if (typeof t.absolute === "number") return `±${t.absolute}`;
  return "";
}

export function calibrationBandView(
  input: CalibrationBandInput | null | undefined,
): CalibrationBandView {
  if (!input || !input.isCalibratedReplay) return HIDDEN;
  const predicted = input.predicted;
  if (typeof predicted !== "number" || !Number.isFinite(predicted)) return HIDDEN;

  const digits =
    typeof input.fractionDigits === "number" && input.fractionDigits >= 0
      ? input.fractionDigits
      : 4;

  const supported =
    input.supported &&
    typeof input.actual === "number" &&
    Number.isFinite(input.actual);
  const actualNum =
    supported && typeof input.actual === "number" ? input.actual : null;

  const modelText = `Model: ${formatNumber(predicted, digits)}`;
  const mainnetText =
    actualNum === null ? "Mainnet: —" : `Mainnet: ${formatNumber(actualNum, digits)}`;

  const delta = actualNum === null ? null : predicted - actualNum;
  const absError = delta === null ? null : Math.abs(delta);
  const relError =
    absError !== null && actualNum !== null && actualNum !== 0
      ? absError / Math.abs(actualNum)
      : null;

  const deltaText = delta === null ? "—" : `Δ = ${formatSigned(delta, digits)}`;
  const relErrorText =
    relError === null ? "" : `(${(relError * 100).toFixed(2)}%)`;

  const threshold = input.threshold ?? null;
  const hasThreshold =
    threshold !== null &&
    threshold !== undefined &&
    (typeof threshold.relative === "number" ||
      typeof threshold.absolute === "number");

  let withinThreshold = false;
  let breached = false;
  let marker = "";
  let thresholdText = "";

  if (!supported) {
    thresholdText = "decoder pending";
    marker = "⏳";
  } else if (!hasThreshold) {
    thresholdText = "no threshold configured";
    marker = "";
  } else {
    const label = formatThresholdLabel(threshold!);
    if (
      typeof threshold!.relative === "number" &&
      relError !== null &&
      relError <= threshold!.relative
    ) {
      withinThreshold = true;
    } else if (
      typeof threshold!.absolute === "number" &&
      absError !== null &&
      absError <= threshold!.absolute
    ) {
      withinThreshold = true;
    } else {
      breached = true;
    }
    marker = withinThreshold ? "✓" : "✗";
    thresholdText = withinThreshold
      ? `within ${label} threshold`
      : `out-of-band ${label} threshold`;
  }

  const trailing = thresholdText ? ` [${thresholdText}${marker ? ` ${marker}` : ""}]` : "";
  const relPart = relErrorText ? ` ${relErrorText}` : "";
  const composed = `${modelText}   ${mainnetText}   ${deltaText}${relPart}${trailing}`.trim();

  return {
    visible: true,
    modelText,
    mainnetText,
    deltaText,
    relErrorText,
    thresholdText,
    marker,
    withinThreshold,
    breached,
    composed,
  };
}
