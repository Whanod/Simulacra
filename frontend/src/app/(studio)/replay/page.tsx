"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { type ReplayDiffMetric } from "@/components/ReplayDiffChart";
import {
  REPLAY_METRIC_LABELS,
  REPLAY_METRIC_ORDER,
  normalizeReplayMetrics,
  type ReplayMetricKey,
  type ReplayMetricResult,
} from "@/components/charts/replay";
import TipPnlSweepChart from "@/components/charts/replay/TipPnlSweepChart";
import Topbar from "@/components/shell/Topbar";
import { toToastMessage } from "@/lib/api/errors";
import { thresholdForMetric } from "@/lib/api/adapters/calibrationBands";
import {
  calibrationService,
  type CalibrationCorpusSlot,
} from "@/lib/services/calibrationService";
import {
  replayService,
  type ReplayBundleTarget,
  type ReplayCounterfactualSpec,
  type ReplayPoolTarget,
  type ReplayRequest,
  type ReplayResult,
  type ReplayTargets,
  type TipPnlSweepPoint,
} from "@/lib/services/replayService";

// Real snapshotter-captured steady-state slot from the canonical
// SOL/USDC Whirlpool corpus at solana-plans/calibration/corpus/420196842/.
const DEFAULT_SLOT = 420_196_842;
const RECENT_SLOT_STORAGE_KEY = "defi-sim:replay:recent-slots";
const MAX_RECENT_SLOTS = 8;
const DEFAULT_TIP_ACCOUNT = "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5";
const SHARE_PARAM_VERSION = "1";
const TIP_PNL_SWEEP_RATIOS = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2] as const;

type SchedulerChoice = "default" | "serial" | "priority" | "custom";
type AgentStrategy = "backrun" | "sandwich";
type SlotMode = "single" | "range";
type CfKey = "tip" | "pool" | "order" | "agent";
type PreviewPane = "result" | "request" | "share";
type ReplayCounterfactualKind = ReplayCounterfactualSpec["kind"];

interface ActiveTipReplacement {
  targetBundleId: string;
  newTipLamports: number;
}

const COUNTERFACTUAL_KINDS: readonly ReplayCounterfactualKind[] = [
  "TipReplaceCounterfactual",
  "FeeReplaceCounterfactual",
  "OrderingReplaceCounterfactual",
  "AgentInjectCounterfactual",
];

const CF_TAB_LABELS: Record<CfKey, string> = {
  tip: "Tip replacement",
  pool: "Pool fee",
  order: "Ordering",
  agent: "Agent inject",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isReplayCounterfactualKind(
  value: unknown,
): value is ReplayCounterfactualKind {
  return (
    typeof value === "string" &&
    COUNTERFACTUAL_KINDS.includes(value as ReplayCounterfactualKind)
  );
}

function parseSlot(value: string): number {
  const normalized = value.trim().replaceAll("_", "");
  const slot = Number(normalized);
  if (!Number.isInteger(slot) || slot < 0) {
    throw new Error("Slot must be a non-negative integer.");
  }
  return slot;
}

function normalizeSlot(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    return value;
  }
  if (typeof value === "string") {
    try {
      return parseSlot(value);
    } catch {
      return null;
    }
  }
  return null;
}

function normalizeCachedSlots(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<number>();
  const slots: number[] = [];
  for (const item of value) {
    const slot = normalizeSlot(item);
    if (slot === null || seen.has(slot)) continue;
    seen.add(slot);
    slots.push(slot);
    if (slots.length >= MAX_RECENT_SLOTS) break;
  }
  return slots;
}

function mergeRecentSlots(current: number[], incoming: number[]): number[] {
  const seen = new Set<number>();
  const next: number[] = [];
  for (const slot of [...incoming, ...current]) {
    if (!Number.isInteger(slot) || slot < 0 || seen.has(slot)) continue;
    seen.add(slot);
    next.push(slot);
    if (next.length >= MAX_RECENT_SLOTS) break;
  }
  return next;
}

function parseShareCounterfactuals(
  encoded: string | null,
): ReplayCounterfactualSpec[] | null {
  if (encoded === null || encoded.trim() === "") return [];
  try {
    const parsed: unknown = JSON.parse(encoded);
    if (!Array.isArray(parsed)) return null;
    const counterfactuals: ReplayCounterfactualSpec[] = [];
    for (const item of parsed) {
      if (
        !isRecord(item) ||
        !isReplayCounterfactualKind(item.kind) ||
        !isRecord(item.params)
      ) {
        return null;
      }
      counterfactuals.push({
        kind: item.kind,
        params: item.params,
      });
    }
    return counterfactuals;
  } catch {
    return null;
  }
}

function hasReplayShareParams(params: URLSearchParams): boolean {
  return (
    params.has("slot") ||
    params.has("start") ||
    params.has("end") ||
    params.has("cf")
  );
}

function parseReplayShareRequest(params: URLSearchParams): ReplayRequest | null {
  const singleSlot = normalizeSlot(params.get("slot"));
  const start = singleSlot ?? normalizeSlot(params.get("start"));
  const end = singleSlot ?? normalizeSlot(params.get("end"));
  if (start === null || end === null || end < start) return null;
  const counterfactuals = parseShareCounterfactuals(params.get("cf"));
  if (counterfactuals === null) return null;
  return {
    slotStart: start,
    slotEnd: end,
    counterfactuals,
  };
}

function buildReplaySharePath(request: ReplayRequest): string {
  const params = new URLSearchParams();
  params.set("v", SHARE_PARAM_VERSION);
  if (request.slotStart === request.slotEnd) {
    params.set("slot", String(request.slotStart));
  } else {
    params.set("start", String(request.slotStart));
    params.set("end", String(request.slotEnd));
  }
  const counterfactuals = request.counterfactuals ?? [];
  if (counterfactuals.length > 0) {
    params.set("cf", JSON.stringify(counterfactuals));
  }
  return `/replay?${params.toString()}`;
}

function stringParam(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nonNegativeIntegerParam(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.max(0, Math.trunc(value));
}

function schedulerParam(value: unknown, fallback: SchedulerChoice): SchedulerChoice {
  return value === "default" ||
    value === "serial" ||
    value === "priority" ||
    value === "custom"
    ? value
    : fallback;
}

function agentStrategyParam(value: unknown, fallback: AgentStrategy): AgentStrategy {
  return value === "backrun" || value === "sandwich" ? value : fallback;
}

function requestIsCorpusBacked(
  request: ReplayRequest | null,
  corpusSlots: CalibrationCorpusSlot[],
): boolean {
  return (
    request !== null &&
    request.slotStart === request.slotEnd &&
    corpusSlots.some((slot) => slot.slot === request.slotStart)
  );
}

function metricFractionDigits(metric?: ReplayMetricResult): number {
  if (!metric) return 2;
  if (metric.unit === "lamports" || metric.unit === "locks") return 0;
  if (metric.unit === "slots") return 2;
  if (metric.unit === "ratio" || metric.unit === "tip/ev") return 3;
  return 2;
}

function replayMetricCalibrationInput(
  result: ReplayResult,
  metricKey: ReplayMetricKey,
  fractionDigits: number,
) {
  const band = result.replayDiff?.per_metric_error?.[metricKey];
  if (!band) return null;
  return {
    isCalibratedReplay: result.mainnetAccuracyClaim,
    predicted: band.predicted,
    actual: band.actual,
    supported: band.supported ?? band.actual !== null,
    threshold: thresholdForMetric(metricKey),
    fractionDigits,
  };
}

function calibrationInput(
  actual: ReplayResult,
  counterfactual: ReplayResult,
  actualValue: number,
  counterfactualValue: number,
  fractionDigits: number,
) {
  return {
    isCalibratedReplay:
      actual.mainnetAccuracyClaim && counterfactual.mainnetAccuracyClaim,
    predicted: counterfactualValue,
    actual: actualValue,
    supported: true,
    threshold: null,
    fractionDigits,
  };
}

function canonicalReplayComparisonMetrics(
  actual: ReplayResult,
  counterfactual: ReplayResult,
): ReplayDiffMetric[] {
  const actualMetrics = normalizeReplayMetrics(actual.replayMetrics);
  const counterfactualMetrics = normalizeReplayMetrics(
    counterfactual.replayMetrics,
  );
  return REPLAY_METRIC_ORDER.flatMap((key) => {
    const actualMetric = actualMetrics[key];
    const counterfactualMetric = counterfactualMetrics[key];
    if (!actualMetric && !counterfactualMetric) return [];
    const unit = counterfactualMetric?.unit ?? actualMetric?.unit;
    const fractionDigits = metricFractionDigits(
      counterfactualMetric ?? actualMetric,
    );
    const counterfactualValue = counterfactualMetric?.value ?? 0;
    return [
      {
        key,
        label: REPLAY_METRIC_LABELS[key],
        actual: actualMetric?.value ?? 0,
        counterfactual: counterfactualValue,
        unit,
        fractionDigits,
        calibrationBand: replayMetricCalibrationInput(
          counterfactual,
          key,
          fractionDigits,
        ),
      },
    ];
  });
}

function diagnosticComparisonMetrics(
  actual: ReplayResult,
  counterfactual: ReplayResult,
): ReplayDiffMetric[] {
  const unsupportedActual = actual.unsupportedProgramIds.length;
  const unsupportedCounterfactual = counterfactual.unsupportedProgramIds.length;
  return [
    {
      key: "decoded_transaction_share",
      label: "Decoded coverage",
      actual: actual.decodedTransactionShare * 100,
      counterfactual: counterfactual.decodedTransactionShare * 100,
      unit: "%",
      fractionDigits: 2,
      calibrationBand: calibrationInput(
        actual,
        counterfactual,
        actual.decodedTransactionShare * 100,
        counterfactual.decodedTransactionShare * 100,
        2,
      ),
    },
    {
      key: "slots_loaded",
      label: "Slots loaded",
      actual: actual.slotsLoaded,
      counterfactual: counterfactual.slotsLoaded,
      calibrationBand: calibrationInput(
        actual,
        counterfactual,
        actual.slotsLoaded,
        counterfactual.slotsLoaded,
        0,
      ),
    },
    {
      key: "unsupported_programs",
      label: "Unsupported programs",
      actual: unsupportedActual,
      counterfactual: unsupportedCounterfactual,
      calibrationBand: calibrationInput(
        actual,
        counterfactual,
        unsupportedActual,
        unsupportedCounterfactual,
        0,
      ),
    },
    {
      key: "counterfactuals",
      label: "Counterfactuals",
      actual: actual.counterfactuals.length,
      counterfactual: counterfactual.counterfactuals.length,
      calibrationBand: calibrationInput(
        actual,
        counterfactual,
        actual.counterfactuals.length,
        counterfactual.counterfactuals.length,
        0,
      ),
    },
  ];
}

function comparisonMetrics(
  actual: ReplayResult,
  counterfactual: ReplayResult,
): ReplayDiffMetric[] {
  const canonical = canonicalReplayComparisonMetrics(actual, counterfactual);
  return canonical.length > 0
    ? canonical
    : diagnosticComparisonMetrics(actual, counterfactual);
}

function tipReplacementFromRequest(
  request: ReplayRequest | null,
): ActiveTipReplacement | null {
  const tipSpec = (request?.counterfactuals ?? []).find(
    (spec) => spec.kind === "TipReplaceCounterfactual",
  );
  if (!tipSpec) return null;
  const targetBundleId = stringParam(tipSpec.params.target_bundle_id).trim();
  if (!targetBundleId) return null;
  return {
    targetBundleId,
    newTipLamports: nonNegativeIntegerParam(tipSpec.params.new_tip_lamports, 0),
  };
}

function roundedTip(value: number): number {
  if (value <= 0) return 0;
  return Math.round(value / 1_000) * 1_000;
}

function relativeTipPnlSweep(
  activeTipReplacement: ActiveTipReplacement | null,
): TipPnlSweepPoint[] {
  if (!activeTipReplacement) return [];
  const activeTip = Math.max(0, Math.trunc(activeTipReplacement.newTipLamports));
  const maxTip = Math.max(activeTip * 2, 100_000);
  const tips = new Set<number>([activeTip]);
  for (const ratio of TIP_PNL_SWEEP_RATIOS) {
    tips.add(roundedTip(maxTip * ratio));
  }
  return [...tips]
    .filter((tip) => tip >= 0)
    .sort((a, b) => a - b)
    .map((tip) => ({
      tipLamports: tip,
      pnlLamports: activeTip - tip,
      mainnetPnlLamports: null,
      landingProbability: null,
    }));
}

function nearestSweepPoint(
  points: TipPnlSweepPoint[],
  activeTipLamports: number | null,
): TipPnlSweepPoint | null {
  if (points.length === 0) return null;
  if (activeTipLamports === null) return points[points.length - 1] ?? null;
  return points.reduce((best, point) =>
    Math.abs(point.tipLamports - activeTipLamports) <
    Math.abs(best.tipLamports - activeTipLamports)
      ? point
      : best,
  );
}

function tipPnlCalibrationInput(
  points: TipPnlSweepPoint[],
  activeTipLamports: number | null,
  actual: ReplayResult,
  counterfactual: ReplayResult,
) {
  const activePoint = nearestSweepPoint(points, activeTipLamports);
  if (!activePoint) return null;
  const actualPnl =
    typeof activePoint.mainnetPnlLamports === "number"
      ? activePoint.mainnetPnlLamports
      : null;
  return {
    isCalibratedReplay:
      actual.mainnetAccuracyClaim && counterfactual.mainnetAccuracyClaim,
    predicted: activePoint.pnlLamports,
    actual: actualPnl,
    supported: actualPnl !== null,
    threshold: null,
    fractionDigits: 0,
  };
}

function formatLamports(value: number): string {
  return value.toLocaleString("en-US");
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function ReplayPage() {
  const [slotInput, setSlotInput] = useState(String(DEFAULT_SLOT));
  const [slotStart, setSlotStart] = useState(String(DEFAULT_SLOT));
  const [slotEnd, setSlotEnd] = useState(String(DEFAULT_SLOT));
  const [slotMode, setSlotMode] = useState<SlotMode>("single");
  const [corpusOpen, setCorpusOpen] = useState(false);
  const [corpusSearch, setCorpusSearch] = useState("");
  const [recentSlots, setRecentSlots] = useState<number[]>([]);
  const [corpusSlots, setCorpusSlots] = useState<CalibrationCorpusSlot[]>([]);
  const [syntheticSlotIds, setSyntheticSlotIds] = useState<Set<number>>(
    () => new Set(),
  );
  const [corpusLoading, setCorpusLoading] = useState(true);
  const [corpusError, setCorpusError] = useState<string | null>(null);

  const [cfTab, setCfTab] = useState<CfKey>("tip");
  const [bundleId, setBundleId] = useState("");
  const [tipLamports, setTipLamports] = useState(0);
  const [feePool, setFeePool] = useState("");
  const [feeBps, setFeeBps] = useState(30);
  const [schedulerChoice, setSchedulerChoice] =
    useState<SchedulerChoice>("default");
  const [agentId, setAgentId] = useState("");
  const [agentStrategy, setAgentStrategy] = useState<AgentStrategy>("backrun");
  const [agentMinEvLamports, setAgentMinEvLamports] = useState(0);
  const [agentTipAccount, setAgentTipAccount] = useState("");

  const [slotTargets, setSlotTargets] = useState<ReplayTargets | null>(null);
  const [slotTargetsSlot, setSlotTargetsSlot] = useState<number | null>(null);
  const [slotTargetsLoading, setSlotTargetsLoading] = useState(false);
  const [slotTargetsError, setSlotTargetsError] = useState<string | null>(null);
  // Track whether the user has hand-edited the CF target so auto-defaulting
  // from the targets fetch doesn't overwrite their pick.
  const bundleIdTouchedRef = useRef(false);
  const feePoolTouchedRef = useRef(false);

  const [previewPane, setPreviewPane] = useState<PreviewPane>("result");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shareNotice, setShareNotice] = useState<string | null>(null);
  const [origin, setOrigin] = useState("");
  const [actualResult, setActualResult] = useState<ReplayResult | null>(null);
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [lastRequest, setLastRequest] = useState<ReplayRequest | null>(null);
  const [lastRunAt, setLastRunAt] = useState<string | null>(null);
  const loadedShareRef = useRef(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(RECENT_SLOT_STORAGE_KEY);
      if (raw === null) return;
      setRecentSlots(normalizeCachedSlots(JSON.parse(raw)));
    } catch {
      setRecentSlots([]);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    calibrationService
      .getCorpus()
      .then((corpus) => {
        if (ignore) return;
        const valid = corpus.slots.filter(
          (slot) => Number.isInteger(slot.slot) && slot.slot >= 0,
        );
        // FIX-016: hide hand-filled synthetic placeholders. Real slots
        // come from the snapshotter (FIX-019).
        setCorpusSlots(
          valid
            .filter((slot) => slot.category !== "synthetic")
            .sort((a, b) => b.slot - a.slot),
        );
        setSyntheticSlotIds(
          new Set(
            valid
              .filter((slot) => slot.category === "synthetic")
              .map((slot) => slot.slot),
          ),
        );
        setCorpusError(null);
      })
      .catch((err: unknown) => {
        if (ignore) return;
        setCorpusSlots([]);
        setCorpusError(toToastMessage(err));
      })
      .finally(() => {
        if (!ignore) setCorpusLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (loadedShareRef.current) return;
    setOrigin(window.location.origin);
    const params = new URLSearchParams(window.location.search);
    if (!hasReplayShareParams(params)) return;
    loadedShareRef.current = true;
    const sharedRequest = parseReplayShareRequest(params);
    if (sharedRequest === null) {
      setError("Share link is invalid.");
      return;
    }
    applyReplayRequest(sharedRequest);
    setShareNotice("Shared replay state loaded.");
    rememberSlots([sharedRequest.slotStart, sharedRequest.slotEnd]);
    void submitReplayRequest(sharedRequest);
  }, []);

  // ⌘↵ to run
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        void handleSubmit();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  });

  const requestPreview = useMemo<ReplayRequest | null>(() => {
    try {
      const start =
        slotMode === "single" ? parseSlot(slotInput) : parseSlot(slotStart);
      const end =
        slotMode === "single" ? parseSlot(slotInput) : parseSlot(slotEnd);
      if (end < start) return null;
      const counterfactuals: ReplayCounterfactualSpec[] = [];
      const normalizedBundleId = bundleId.trim();
      const normalizedPool = feePool.trim();
      const normalizedAgentId = agentId.trim();
      const normalizedTipAccount = agentTipAccount.trim();

      if (normalizedBundleId) {
        counterfactuals.push({
          kind: "TipReplaceCounterfactual",
          params: {
            target_bundle_id: normalizedBundleId,
            new_tip_lamports: Math.max(0, Math.trunc(tipLamports)),
          },
        });
      }
      if (normalizedPool) {
        counterfactuals.push({
          kind: "FeeReplaceCounterfactual",
          params: {
            target_pool: normalizedPool,
            new_fee_bps: Math.max(0, Math.trunc(feeBps)),
          },
        });
      }
      if (schedulerChoice !== "default") {
        counterfactuals.push({
          kind: "OrderingReplaceCounterfactual",
          params: {
            scheduler: { type: schedulerChoice },
          },
        });
      }
      if (normalizedAgentId && normalizedTipAccount) {
        counterfactuals.push({
          kind: "AgentInjectCounterfactual",
          params: {
            agent_type: "jito_searcher",
            agent_id: normalizedAgentId,
            strategy: agentStrategy,
            min_ev_to_submit_lamports: Math.max(
              0,
              Math.trunc(agentMinEvLamports),
            ),
            tip_account: normalizedTipAccount,
          },
        });
      }
      return { slotStart: start, slotEnd: end, counterfactuals };
    } catch {
      return null;
    }
  }, [
    slotMode,
    slotInput,
    slotStart,
    slotEnd,
    bundleId,
    tipLamports,
    feePool,
    feeBps,
    schedulerChoice,
    agentId,
    agentStrategy,
    agentMinEvLamports,
    agentTipAccount,
  ]);

  const cfActive: Record<CfKey, boolean> = {
    tip: bundleId.trim().length > 0,
    pool: feePool.trim().length > 0,
    order: schedulerChoice !== "default",
    agent: agentId.trim().length > 0 && agentTipAccount.trim().length > 0,
  };
  const cfActiveCount = Object.values(cfActive).filter(Boolean).length;

  const activeSingleSlot = useMemo(() => {
    try {
      if (slotMode === "single") return parseSlot(slotInput);
      const start = parseSlot(slotStart);
      const end = parseSlot(slotEnd);
      return start === end ? start : null;
    } catch {
      return null;
    }
  }, [slotMode, slotInput, slotStart, slotEnd]);

  useEffect(() => {
    if (activeSingleSlot === null) {
      setSlotTargets(null);
      setSlotTargetsSlot(null);
      setSlotTargetsError(null);
      setSlotTargetsLoading(false);
      return;
    }
    if (slotTargetsSlot === activeSingleSlot) return;
    // Slot changed (transition from a previously-loaded slot): wipe the
    // CF target fields so values picked for the old slot don't silently
    // no-op against the new one. On the very first slot load, preserve
    // any values set by share-link / applyReplayRequest before the
    // effect fires (touched refs).
    const slotTransition = slotTargetsSlot !== null;
    if (slotTransition) {
      setBundleId("");
      setTipLamports(0);
      bundleIdTouchedRef.current = false;
      setFeePool("");
      feePoolTouchedRef.current = false;
    }
    setSlotTargets(null);
    let ignore = false;
    setSlotTargetsLoading(true);
    setSlotTargetsError(null);
    replayService
      .getTargets(activeSingleSlot)
      .then((targets) => {
        if (ignore) return;
        setSlotTargets(targets);
        setSlotTargetsSlot(activeSingleSlot);
        if (!bundleIdTouchedRef.current && targets.bundles.length > 0) {
          setBundleId(targets.bundles[0].bundleId);
          setTipLamports(targets.bundles[0].tipLamports);
        }
        if (!feePoolTouchedRef.current && targets.pools.length > 0) {
          setFeePool(targets.pools[0].poolId);
        }
      })
      .catch((err: unknown) => {
        if (ignore) return;
        setSlotTargets(null);
        setSlotTargetsSlot(activeSingleSlot);
        setSlotTargetsError(toToastMessage(err));
      })
      .finally(() => {
        if (!ignore) setSlotTargetsLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [activeSingleSlot, slotTargetsSlot]);

  const sharePath = useMemo(
    () => (requestPreview ? buildReplaySharePath(requestPreview) : null),
    [requestPreview],
  );
  const shareUrl =
    sharePath === null ? "" : origin ? `${origin}${sharePath}` : sharePath;
  const shareIsPermanent = requestIsCorpusBacked(requestPreview, corpusSlots);
  const activeTipReplacement = useMemo(
    () => tipReplacementFromRequest(lastRequest),
    [lastRequest],
  );
  const activeTipLamports = activeTipReplacement?.newTipLamports ?? null;
  const tipPnlSweepPoints = useMemo(() => {
    if (!result) return [];
    if (result.tipPnlSweep.length > 0) return result.tipPnlSweep;
    return relativeTipPnlSweep(activeTipReplacement);
  }, [activeTipReplacement, result]);
  const tipPnlCalibrationBand = useMemo(
    () =>
      actualResult && result
        ? tipPnlCalibrationInput(
            tipPnlSweepPoints,
            activeTipLamports,
            actualResult,
            result,
          )
        : null,
    [actualResult, activeTipLamports, result, tipPnlSweepPoints],
  );

  const filteredCorpusSlots = useMemo(() => {
    const q = corpusSearch.trim().toLowerCase();
    if (!q) return corpusSlots;
    return corpusSlots.filter((s) => String(s.slot).includes(q));
  }, [corpusSearch, corpusSlots]);

  // FIX-016: the Recent strip is sourced from localStorage and may
  // carry synthetic-corpus slots from prior sessions. Drop synthetic
  // slots, then merge with the newest real corpus slots so fresh
  // snapshotter captures always surface alongside the user's recents.
  const filteredRecentSlots = useMemo(() => {
    const cleaned = recentSlots.filter((slot) => !syntheticSlotIds.has(slot));
    const corpusFresh = corpusSlots.map((s) => s.slot);
    const seen = new Set<number>();
    const merged: number[] = [];
    for (const slot of [...cleaned, ...corpusFresh]) {
      if (seen.has(slot)) continue;
      seen.add(slot);
      merged.push(slot);
      if (merged.length >= MAX_RECENT_SLOTS) break;
    }
    return merged;
  }, [recentSlots, syntheticSlotIds, corpusSlots]);

  function rememberSlots(slots: number[]) {
    setRecentSlots((current) => {
      const next = mergeRecentSlots(current, slots);
      try {
        window.localStorage.setItem(RECENT_SLOT_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore
      }
      return next;
    });
  }

  function applyReplayRequest(request: ReplayRequest) {
    const start = String(request.slotStart);
    const end = String(request.slotEnd);
    setSlotInput(start);
    setSlotStart(start);
    setSlotEnd(end);
    setSlotMode(request.slotStart === request.slotEnd ? "single" : "range");
    // Start cleared so the share-link's CFs are the only ones that re-engage.
    setBundleId("");
    bundleIdTouchedRef.current = false;
    setTipLamports(0);
    setFeePool("");
    feePoolTouchedRef.current = false;
    setFeeBps(30);
    setSchedulerChoice("default");
    setAgentId("");
    setAgentStrategy("backrun");
    setAgentMinEvLamports(0);
    setAgentTipAccount("");
    setError(null);
    setActualResult(null);
    setResult(null);
    setLastRequest(null);

    for (const counterfactual of request.counterfactuals ?? []) {
      const params = counterfactual.params;
      if (counterfactual.kind === "TipReplaceCounterfactual") {
        setBundleId(stringParam(params.target_bundle_id));
        bundleIdTouchedRef.current = true;
        setTipLamports(nonNegativeIntegerParam(params.new_tip_lamports, 0));
        setCfTab("tip");
      } else if (counterfactual.kind === "FeeReplaceCounterfactual") {
        setFeePool(stringParam(params.target_pool, "sol_usdc"));
        feePoolTouchedRef.current = true;
        setFeeBps(nonNegativeIntegerParam(params.new_fee_bps, 30));
      } else if (counterfactual.kind === "OrderingReplaceCounterfactual") {
        const scheduler = isRecord(params.scheduler) ? params.scheduler : {};
        setSchedulerChoice(schedulerParam(scheduler.type, "serial"));
      } else if (counterfactual.kind === "AgentInjectCounterfactual") {
        setAgentId(stringParam(params.agent_id, "jito-searcher-cf"));
        setAgentStrategy(agentStrategyParam(params.strategy, "backrun"));
        setAgentMinEvLamports(
          nonNegativeIntegerParam(params.min_ev_to_submit_lamports, 100_000),
        );
        setAgentTipAccount(
          stringParam(params.tip_account, DEFAULT_TIP_ACCOUNT),
        );
      }
    }
  }

  function applySingleSlot(slot: number) {
    const value = String(slot);
    setSlotInput(value);
    setSlotStart(value);
    setSlotEnd(value);
    setSlotMode("single");
    setError(null);
    setShareNotice(null);
    rememberSlots([slot]);
  }

  function handleSlotApply() {
    try {
      if (slotMode === "single") {
        applySingleSlot(parseSlot(slotInput));
      } else {
        const start = parseSlot(slotStart);
        const end = parseSlot(slotEnd);
        if (end < start) {
          throw new Error("Slot range end must be ≥ start.");
        }
        rememberSlots([start, end]);
        setError(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Slot is invalid.");
    }
  }

  async function handleCopyShareLink() {
    if (sharePath === null) return;
    const absoluteUrl = new URL(sharePath, window.location.href).toString();
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API unavailable.");
      }
      await navigator.clipboard.writeText(absoluteUrl);
      setShareNotice("Share link copied.");
    } catch {
      setShareNotice("Copy unavailable; select the link field.");
    }
  }

  async function handleSubmit() {
    if (!requestPreview) {
      setError("Slot range is invalid.");
      return;
    }
    await submitReplayRequest(requestPreview);
  }

  function handleReset() {
    setSlotInput(String(DEFAULT_SLOT));
    setSlotStart(String(DEFAULT_SLOT));
    setSlotEnd(String(DEFAULT_SLOT));
    setSlotMode("single");
    setBundleId("");
    bundleIdTouchedRef.current = false;
    setTipLamports(0);
    setFeePool("");
    feePoolTouchedRef.current = false;
    setFeeBps(30);
    setSchedulerChoice("default");
    setAgentId("");
    setAgentStrategy("backrun");
    setAgentMinEvLamports(0);
    setAgentTipAccount("");
    setError(null);
    setShareNotice(null);
    setActualResult(null);
    setResult(null);
    setLastRequest(null);
    setLastRunAt(null);
    setCfTab("tip");
  }

  async function submitReplayRequest(request: ReplayRequest) {
    setError(null);
    setActualResult(null);
    setResult(null);
    setSubmitting(true);
    setLastRequest(request);
    rememberSlots([request.slotStart, request.slotEnd]);
    try {
      const baselineRequest: ReplayRequest = {
        ...request,
        counterfactuals: [],
      };
      const baseline = await replayService.submitReplay(baselineRequest);
      const activeCounterfactuals = request.counterfactuals ?? [];
      const counterfactual =
        activeCounterfactuals.length > 0
          ? await replayService.submitReplay(request)
          : baseline;
      setActualResult(baseline);
      setResult(counterfactual);
      setLastRunAt(formatTime(new Date()));
      setPreviewPane("result");
    } catch (err) {
      setError(toToastMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  // -------- Render --------
  return (
    <>
      <Topbar title="Replay" spec={{ execution: { model: "solana_like" } }} />

      <div id="content" className="fade-in" data-testid="replay-page">
        <div className="replay-builder-grid">
          {/* LEFT — config */}
          <div className="replay-col">
            {/* Step 1 — Slot picker */}
            <div className="replay-pane">
              <div className="replay-pane-head">
                <div className="replay-pane-head-left">
                  <span className="replay-step-bubble">1</span>
                  <span className="replay-pane-title">Pick a slot</span>
                  <span className="replay-pane-sub">
                    — mainnet, devnet, or calibration corpus
                  </span>
                </div>
                {shareIsPermanent ? (
                  <span className="replay-corpus-flag">Corpus-backed</span>
                ) : null}
              </div>

              <div className="replay-slot-row">
                <div className="replay-slot-input-wrap">
                  <input
                    data-testid="replay-slot-search"
                    inputMode="numeric"
                    placeholder="slot number"
                    value={slotInput}
                    onChange={(e) => {
                      setSlotInput(e.target.value);
                      if (slotMode === "single") {
                        setSlotStart(e.target.value);
                        setSlotEnd(e.target.value);
                      }
                    }}
                  />
                  <button
                    type="button"
                    className="replay-corpus-trigger"
                    onClick={() => setCorpusOpen((v) => !v)}
                  >
                    Corpus ▾
                  </button>
                </div>
                <div className="replay-seg" role="tablist">
                  <button
                    type="button"
                    className={slotMode === "single" ? "on" : ""}
                    onClick={() => setSlotMode("single")}
                  >
                    Single slot
                  </button>
                  <button
                    type="button"
                    className={slotMode === "range" ? "on" : ""}
                    onClick={() => setSlotMode("range")}
                  >
                    Range
                  </button>
                </div>
                <button
                  className="btn btn-secondary"
                  data-testid="replay-slot-apply"
                  onClick={handleSlotApply}
                >
                  Use slot
                </button>
              </div>

              {slotMode === "range" ? (
                <div className="grid-2" style={{ marginTop: 12 }}>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label htmlFor="replay-slot-start">Slot start</label>
                    <input
                      id="replay-slot-start"
                      data-testid="replay-slot-start"
                      inputMode="numeric"
                      value={slotStart}
                      onChange={(e) => setSlotStart(e.target.value)}
                    />
                  </div>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label htmlFor="replay-slot-end">Slot end</label>
                    <input
                      id="replay-slot-end"
                      data-testid="replay-slot-end"
                      inputMode="numeric"
                      value={slotEnd}
                      onChange={(e) => setSlotEnd(e.target.value)}
                    />
                  </div>
                </div>
              ) : (
                <>
                  <input
                    type="hidden"
                    data-testid="replay-slot-start"
                    value={slotStart}
                    readOnly
                  />
                  <input
                    type="hidden"
                    data-testid="replay-slot-end"
                    value={slotEnd}
                    readOnly
                  />
                </>
              )}

              {corpusOpen ? (
                <div className="replay-corpus-panel" data-testid="replay-famous-slots">
                  <div className="replay-corpus-panel-head">
                    <input
                      placeholder="Search corpus slots…"
                      value={corpusSearch}
                      onChange={(e) => setCorpusSearch(e.target.value)}
                    />
                    <span className="count">
                      {corpusLoading
                        ? "loading…"
                        : `${filteredCorpusSlots.length} slots`}
                    </span>
                  </div>
                  <div className="replay-corpus-list">
                    {!corpusLoading && filteredCorpusSlots.length === 0 ? (
                      <div className="replay-corpus-row">
                        <span className="hint">
                          {corpusError ?? "No corpus slots."}
                        </span>
                      </div>
                    ) : null}
                    {filteredCorpusSlots.map((slot) => {
                      const selected = activeSingleSlot === slot.slot;
                      return (
                        <div
                          key={slot.slot}
                          className={`replay-corpus-row${selected ? " sel" : ""}`}
                          data-testid="replay-famous-slot"
                          onClick={() => {
                            applySingleSlot(slot.slot);
                            setCorpusOpen(false);
                          }}
                        >
                          <div>
                            <strong>{slot.slot}</strong>
                            <small>
                              {slot.category ?? "uncategorized"}
                              {slot.programs.length > 0
                                ? ` · ${slot.programs.length} program${
                                    slot.programs.length === 1 ? "" : "s"
                                  }`
                                : ""}
                            </small>
                          </div>
                          <span className="hint">
                            {selected ? "selected" : "use ↵"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {filteredRecentSlots.length > 0 ? (
                <div className="replay-recent-row" data-testid="replay-recent-slots">
                  <span className="label">Recent</span>
                  {filteredRecentSlots.map((slot) => (
                    <button
                      key={slot}
                      type="button"
                      data-testid="replay-recent-slot"
                      className={`replay-recent-chip${
                        activeSingleSlot === slot ? " active" : ""
                      }`}
                      onClick={() => applySingleSlot(slot)}
                    >
                      {slot}
                    </button>
                  ))}
                  <span className="replay-recent-foot">— last 24h</span>
                </div>
              ) : null}
            </div>

            {/* Step 2 — Counterfactuals */}
            <div className="replay-pane">
              <div className="replay-pane-head">
                <div className="replay-pane-head-left">
                  <span className="replay-step-bubble">2</span>
                  <span className="replay-pane-title">Counterfactuals</span>
                  <span className="replay-pane-sub">
                    — twist one variable, hold the rest constant
                  </span>
                </div>
              </div>

              <div className="replay-cf-tabs" role="tablist">
                {(["tip", "pool", "order", "agent"] as CfKey[]).map((key) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={cfTab === key}
                    className={`replay-cf-tab${cfTab === key ? " active" : ""}`}
                    onClick={() => setCfTab(key)}
                  >
                    <span className="pip" />
                    {CF_TAB_LABELS[key]}
                    {cfActive[key] ? <span className="badge">on</span> : null}
                  </button>
                ))}
              </div>

              {cfTab === "tip" ? (
                <div role="tabpanel">
                  <p className="replay-cf-blurb">
                    Replace a bundle&rsquo;s tip and re-run. Pick a bundle from
                    this slot, or leave empty to keep the original tip.
                  </p>
                  <BundleTargetPicker
                    bundleId={bundleId}
                    onPick={(bundle) => {
                      setBundleId(bundle.bundleId);
                      bundleIdTouchedRef.current = true;
                      setTipLamports(bundle.tipLamports);
                    }}
                    onChange={(value) => {
                      setBundleId(value);
                      bundleIdTouchedRef.current = true;
                    }}
                    targets={slotTargets?.bundles ?? []}
                    loading={slotTargetsLoading}
                    error={slotTargetsError}
                    activeSlot={activeSingleSlot}
                  />
                  <div className="replay-field">
                    <label htmlFor="replay-tip-slider">
                      New tip<span className="unit">lamports</span>
                    </label>
                    <input
                      id="replay-tip-slider"
                      data-testid="replay-tip-slider"
                      type="range"
                      min={0}
                      max={500_000}
                      step={1_000}
                      value={tipLamports}
                      onChange={(e) => setTipLamports(Number(e.target.value))}
                    />
                    <input
                      data-testid="replay-tip-new-lamports"
                      aria-label="New tip value"
                      type="number"
                      min={0}
                      value={tipLamports}
                      onChange={(e) =>
                        setTipLamports(Math.max(0, Number(e.target.value) || 0))
                      }
                    />
                  </div>
                </div>
              ) : null}

              {cfTab === "pool" ? (
                <div role="tabpanel">
                  <p className="replay-cf-blurb">
                    Force a fee tier on a target pool. Pick a pool from this
                    slot, or leave empty to keep mainnet&rsquo;s fee.
                  </p>
                  <PoolTargetPicker
                    feePool={feePool}
                    onPick={(pool) => {
                      setFeePool(pool.poolId);
                      feePoolTouchedRef.current = true;
                    }}
                    onChange={(value) => {
                      setFeePool(value);
                      feePoolTouchedRef.current = true;
                    }}
                    targets={slotTargets?.pools ?? []}
                    loading={slotTargetsLoading}
                    error={slotTargetsError}
                    activeSlot={activeSingleSlot}
                  />
                  <div className="replay-field">
                    <label htmlFor="replay-fee-bps-slider">
                      Fee<span className="unit">bps</span>
                    </label>
                    <input
                      id="replay-fee-bps-slider"
                      data-testid="replay-fee-bps-slider"
                      type="range"
                      min={0}
                      max={500}
                      step={1}
                      value={feeBps}
                      onChange={(e) => setFeeBps(Number(e.target.value))}
                    />
                    <input
                      data-testid="replay-fee-bps-input"
                      aria-label="Fee value"
                      type="number"
                      min={0}
                      value={feeBps}
                      onChange={(e) =>
                        setFeeBps(Math.max(0, Number(e.target.value) || 0))
                      }
                    />
                  </div>
                </div>
              ) : null}

              {cfTab === "order" ? (
                <div role="tabpanel">
                  <p className="replay-cf-blurb">
                    Override how transactions are scheduled inside the slot. Pick{" "}
                    <strong>Default</strong> to keep mainnet&rsquo;s ordering.
                  </p>
                  <div className="replay-seg">
                    {(
                      ["default", "serial", "priority", "custom"] as SchedulerChoice[]
                    ).map((choice) => (
                      <button
                        key={choice}
                        type="button"
                        className={schedulerChoice === choice ? "on" : ""}
                        onClick={() => setSchedulerChoice(choice)}
                      >
                        {choice[0].toUpperCase() + choice.slice(1)}
                      </button>
                    ))}
                  </div>
                  <select
                    data-testid="replay-scheduler-select"
                    value={schedulerChoice}
                    style={{ display: "none" }}
                    onChange={(e) =>
                      setSchedulerChoice(e.target.value as SchedulerChoice)
                    }
                  >
                    <option value="default">Default</option>
                    <option value="serial">Serial</option>
                    <option value="priority">Priority</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
              ) : null}

              {cfTab === "agent" ? (
                <div role="tabpanel">
                  <p className="replay-cf-blurb">
                    Drop a synthetic searcher into the replay. Leave the agent
                    ID or tip account empty to skip this counterfactual.
                  </p>
                  <div className="replay-field-cols">
                    <div className="stack">
                      <label htmlFor="replay-agent-id">Agent ID</label>
                      <input
                        id="replay-agent-id"
                        data-testid="replay-agent-id"
                        value={agentId}
                        onChange={(e) => setAgentId(e.target.value)}
                        placeholder="e.g. jito-searcher-cf"
                      />
                    </div>
                    <div className="stack">
                      <label htmlFor="replay-agent-strategy">Strategy</label>
                      <select
                        id="replay-agent-strategy"
                        data-testid="replay-agent-strategy"
                        value={agentStrategy}
                        onChange={(e) =>
                          setAgentStrategy(e.target.value as AgentStrategy)
                        }
                      >
                        <option value="backrun">Backrun</option>
                        <option value="sandwich">Sandwich</option>
                      </select>
                    </div>
                  </div>
                  <div className="replay-field">
                    <label htmlFor="replay-agent-min-ev-slider">
                      Min EV<span className="unit">lamports</span>
                    </label>
                    <input
                      id="replay-agent-min-ev-slider"
                      data-testid="replay-agent-min-ev-slider"
                      type="range"
                      min={0}
                      max={500_000}
                      step={10_000}
                      value={agentMinEvLamports}
                      onChange={(e) =>
                        setAgentMinEvLamports(Number(e.target.value))
                      }
                    />
                    <input
                      data-testid="replay-agent-min-ev-input"
                      aria-label="Minimum EV value"
                      type="number"
                      min={0}
                      value={agentMinEvLamports}
                      onChange={(e) =>
                        setAgentMinEvLamports(
                          Math.max(0, Number(e.target.value) || 0),
                        )
                      }
                    />
                  </div>
                  <div className="replay-field-cols">
                    <div className="stack" style={{ gridColumn: "1 / -1" }}>
                      <label htmlFor="replay-agent-tip-account">Tip account</label>
                      <input
                        id="replay-agent-tip-account"
                        data-testid="replay-agent-tip-account"
                        value={agentTipAccount}
                        onChange={(e) => setAgentTipAccount(e.target.value)}
                        placeholder={`e.g. ${DEFAULT_TIP_ACCOUNT}`}
                      />
                    </div>
                  </div>
                </div>
              ) : null}
            </div>

            {error ? (
              <div className="bundle-error" data-testid="replay-error">
                {error}
              </div>
            ) : null}

            {/* Sticky run strip */}
            <div className="replay-run-strip">
              <div className="meta">
                <span className="mono">POST /v1/replay</span>
                <span>·</span>
                <span>
                  <span className="pulse">{cfActiveCount}</span> counterfactual
                  {cfActiveCount === 1 ? "" : "s"} active
                </span>
                <span>·</span>
                <span>est. ~1.2s</span>
              </div>
              <div className="actions">
                <button
                  className="btn btn-secondary btn-sm"
                  type="button"
                  onClick={handleReset}
                >
                  Reset
                </button>
                <button className="btn btn-secondary btn-sm" type="button">
                  Save preset
                </button>
                <button
                  className="btn btn-primary cta-primary btn-sm"
                  data-testid="replay-submit"
                  disabled={submitting || requestPreview === null}
                  onClick={handleSubmit}
                >
                  {submitting ? "Running…" : "▶ Run replay"}
                  <span className="kbd">⌘↵</span>
                </button>
              </div>
            </div>
          </div>

          {/* RIGHT — preview/result */}
          <div className="replay-col">
            <div className="replay-preview">
              <div className="replay-preview-head">
                <div className="replay-preview-tabs">
                  <button
                    type="button"
                    className={`replay-preview-tab${
                      previewPane === "result" ? " active" : ""
                    }`}
                    onClick={() => setPreviewPane("result")}
                  >
                    Replay result
                  </button>
                  <button
                    type="button"
                    className={`replay-preview-tab${
                      previewPane === "request" ? " active" : ""
                    }`}
                    onClick={() => setPreviewPane("request")}
                  >
                    Request <span className="badge">JSON</span>
                  </button>
                  <button
                    type="button"
                    className={`replay-preview-tab${
                      previewPane === "share" ? " active" : ""
                    }`}
                    onClick={() => setPreviewPane("share")}
                  >
                    Share
                  </button>
                </div>
                <span className="replay-preview-foot">auto-refreshes after run</span>
              </div>

              {previewPane === "result" ? (
                <div className="replay-preview-body result-body">
                  {result ? (
                    <ReplayResultBody
                      result={result}
                      actualResult={actualResult}
                      lastRunAt={lastRunAt}
                      tipPnlSweepPoints={tipPnlSweepPoints}
                      activeTipLamports={activeTipLamports}
                      tipPnlCalibrationBand={tipPnlCalibrationBand}
                      lastRequest={lastRequest}
                      activeTipReplacement={activeTipReplacement}
                      onCopyShareLink={handleCopyShareLink}
                      onReset={handleReset}
                      sharePathDisabled={sharePath === null}
                      cfActiveCount={cfActiveCount}
                    />
                  ) : (
                    <div
                      className="bundle-empty-state"
                      data-testid="replay-empty-result"
                    >
                      No replay run yet. Configure counterfactuals and press{" "}
                      <strong>Run replay</strong>.
                    </div>
                  )}
                </div>
              ) : null}

              {previewPane === "request" ? (
                <div className="replay-preview-body">
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span className="replay-preview-foot">
                      live preview · updates as you edit
                    </span>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => {
                        if (requestPreview) {
                          void navigator.clipboard?.writeText(
                            JSON.stringify(requestPreview, null, 2),
                          );
                        }
                      }}
                    >
                      Copy
                    </button>
                  </div>
                  <pre
                    className="replay-json-preview"
                    data-testid="replay-request-preview"
                  >
                    {requestPreview
                      ? JSON.stringify(requestPreview, null, 2)
                      : "// invalid request"}
                  </pre>
                </div>
              ) : null}

              {previewPane === "share" ? (
                <div className="replay-preview-body" data-testid="replay-share-link">
                  <span className="replay-section-label">
                    {shareIsPermanent
                      ? "Permanent link for curated corpus slots"
                      : "Ephemeral link for non-corpus replay state"}
                  </span>
                  <div className="replay-share-row-redesign">
                    <input
                      data-testid="replay-share-url"
                      readOnly
                      value={shareUrl}
                      onFocus={(event) => event.currentTarget.select()}
                    />
                    <button
                      className="btn btn-secondary btn-sm"
                      type="button"
                      data-testid="replay-share-copy"
                      disabled={sharePath === null}
                      onClick={handleCopyShareLink}
                    >
                      Copy
                    </button>
                    <Link
                      className="btn btn-secondary btn-sm"
                      data-testid="replay-share-open"
                      href={sharePath ?? "/replay"}
                    >
                      Open ↗
                    </Link>
                  </div>
                  <p className="replay-share-info" data-testid="replay-share-scope">
                    {shareIsPermanent
                      ? "Permanent for curated corpus slots."
                      : "Ephemeral for non-corpus replay state."}
                  </p>
                  {shareNotice ? (
                    <p
                      className="replay-share-info"
                      data-testid="replay-share-notice"
                    >
                      {shareNotice}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

interface ReplayResultBodyProps {
  result: ReplayResult;
  actualResult: ReplayResult | null;
  lastRunAt: string | null;
  tipPnlSweepPoints: TipPnlSweepPoint[];
  activeTipLamports: number | null;
  tipPnlCalibrationBand: ReturnType<typeof tipPnlCalibrationInput> | null;
  lastRequest: ReplayRequest | null;
  activeTipReplacement: ActiveTipReplacement | null;
  onCopyShareLink: () => void;
  onReset: () => void;
  sharePathDisabled: boolean;
  cfActiveCount: number;
}

type ResultSubtab = "overview" | "metrics" | "trace";

const LOWER_IS_BETTER_KEYS = new Set<string>([
  "skip_rate_cost",
  "slot_inclusion_latency",
  "write_lock_heatmap",
  "unsupported_programs",
]);

function formatMetricValue(metric: ReplayDiffMetric, value: number): string {
  const digits = metric.fractionDigits ?? 2;
  const formatted = value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return metric.unit ? `${formatted} ${metric.unit}` : formatted;
}

function deltaTone(metric: ReplayDiffMetric, delta: number): "good" | "bad" | "neutral" {
  if (delta === 0) return "neutral";
  const lowerBetter = LOWER_IS_BETTER_KEYS.has(metric.key);
  const positive = delta > 0;
  return positive === !lowerBetter ? "good" : "bad";
}

function MetricCell({ metric }: { metric: ReplayDiffMetric }) {
  const delta = metric.counterfactual - metric.actual;
  const tone = deltaTone(metric, delta);
  const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "·";
  const digits = metric.fractionDigits ?? 2;
  const deltaStr = `${delta > 0 ? "+" : ""}${delta.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
  return (
    <div
      className="replay-metric-cell"
      data-testid="replay-diff-metric"
      data-metric-key={metric.key}
    >
      <div className="lbl">{metric.label}</div>
      <div className="row">
        <span className="val">{formatMetricValue(metric, metric.counterfactual)}</span>
        <span className={`delta ${tone}`}>
          {arrow} {deltaStr}
        </span>
      </div>
      <div className="baseline">
        baseline {formatMetricValue(metric, metric.actual)}
      </div>
    </div>
  );
}

function ReplayResultBody({
  result,
  actualResult,
  lastRunAt,
  tipPnlSweepPoints,
  activeTipLamports,
  tipPnlCalibrationBand,
  lastRequest,
  activeTipReplacement,
  onCopyShareLink,
  onReset,
  sharePathDisabled,
  cfActiveCount,
}: ReplayResultBodyProps) {
  const [subtab, setSubtab] = useState<ResultSubtab>("overview");
  const calibrated = result.eligibleForCalibration;
  const metrics = actualResult ? comparisonMetrics(actualResult, result) : [];

  const decodedDelta = actualResult
    ? result.decodedTransactionShare - actualResult.decodedTransactionShare
    : null;
  const slotsDelta = actualResult
    ? result.slotsLoaded - actualResult.slotsLoaded
    : null;

  const slotLabel =
    result.slotRange[0] === result.slotRange[1]
      ? String(result.slotRange[0])
      : `${result.slotRange[0]}–${result.slotRange[1]}`;

  return (
    <div data-testid="replay-result" style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      {/* Persistent headline strip */}
      <div className="replay-headline">
        <div className="replay-headline-meta">
          <div className="top">
            <span className={`badge-cal${calibrated ? "" : " partial"}`}>
              {calibrated ? "Calibrated" : "Partial"}
            </span>
            <span className="slot">slot {slotLabel}</span>
          </div>
          <div className="ts">last run · {lastRunAt ?? formatTime(new Date())}</div>
        </div>
        <div className="replay-headline-kpis">
          <div className="replay-headline-kpi">
            <div className="lbl">Tip applied</div>
            <div className="val">
              {activeTipReplacement
                ? formatLamports(activeTipReplacement.newTipLamports)
                : "—"}
            </div>
            <div className="delta">
              {activeTipReplacement
                ? `${formatLamports(activeTipReplacement.newTipLamports)} lamports`
                : "no tip CF"}
            </div>
          </div>
          <div className="replay-headline-kpi">
            <div className="lbl">Decoded</div>
            <div className="val">
              {(result.decodedTransactionShare * 100).toFixed(1)}%
            </div>
            <div
              className={`delta${
                decodedDelta === null
                  ? ""
                  : decodedDelta > 0
                    ? " good"
                    : decodedDelta < 0
                      ? " bad"
                      : ""
              }`}
            >
              {decodedDelta === null
                ? "—"
                : `${decodedDelta >= 0 ? "+" : ""}${(decodedDelta * 100).toFixed(2)} pp`}
            </div>
          </div>
          <div className="replay-headline-kpi">
            <div className="lbl">Slots loaded</div>
            <div className="val">{result.slotsLoaded}</div>
            <div
              className={`delta${
                slotsDelta === null
                  ? ""
                  : slotsDelta > 0
                    ? " good"
                    : slotsDelta < 0
                      ? " bad"
                      : ""
              }`}
            >
              {slotsDelta === null
                ? "—"
                : `${slotsDelta >= 0 ? "+" : ""}${slotsDelta} vs baseline`}
            </div>
          </div>
        </div>
      </div>

      {/* Sub-tabs */}
      <div className="replay-subtabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={subtab === "overview"}
          className={`replay-subtab${subtab === "overview" ? " active" : ""}`}
          onClick={() => setSubtab("overview")}
        >
          Overview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subtab === "metrics"}
          className={`replay-subtab${subtab === "metrics" ? " active" : ""}`}
          onClick={() => setSubtab("metrics")}
        >
          Metrics
          {metrics.length > 0 ? <span className="count">{metrics.length}</span> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={subtab === "trace"}
          className={`replay-subtab${subtab === "trace" ? " active" : ""}`}
          onClick={() => setSubtab("trace")}
        >
          Trace
        </button>
      </div>

      {/* Overview */}
      {subtab === "overview" ? (
        <div className="subtab-pane" role="tabpanel">
          <div className="replay-changed-card">
            <div className="head">
              <span className="title">What changed vs baseline</span>
              <span className="sub">
                {cfActiveCount} CF{cfActiveCount === 1 ? "" : "s"} active
              </span>
            </div>
            <div className="replay-changed-list">
              {activeTipReplacement ? (
                <div className="row">
                  <span className="key">tip</span>
                  <span className="delta">
                    <span className="strike">0</span>
                    <span className="new">
                      {formatLamports(activeTipReplacement.newTipLamports)}
                    </span>
                  </span>
                </div>
              ) : null}
              {actualResult ? (
                <>
                  <div className="row">
                    <span className="key">decoded share</span>
                    <span className="delta">
                      <span className="strike">
                        {(actualResult.decodedTransactionShare * 100).toFixed(2)}%
                      </span>
                      <span
                        className={`new${
                          result.decodedTransactionShare <
                          actualResult.decodedTransactionShare
                            ? " bad"
                            : ""
                        }`}
                      >
                        {(result.decodedTransactionShare * 100).toFixed(2)}%
                      </span>
                    </span>
                  </div>
                  <div className="row">
                    <span className="key">slots loaded</span>
                    <span className="delta">
                      <span className="strike">{actualResult.slotsLoaded}</span>
                      <span
                        className={`new${
                          result.slotsLoaded < actualResult.slotsLoaded
                            ? " bad"
                            : ""
                        }`}
                      >
                        {result.slotsLoaded}
                      </span>
                    </span>
                  </div>
                  <div className="row">
                    <span className="key">counterfactuals</span>
                    <span className="delta">
                      <span className="strike">
                        {actualResult.counterfactuals.length}
                      </span>
                      <span className="new">{result.counterfactuals.length}</span>
                    </span>
                  </div>
                </>
              ) : (
                <div className="empty">no baseline to diff</div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* Metrics */}
      {subtab === "metrics" ? (
        <div className="subtab-pane" role="tabpanel">
          {actualResult && metrics.length > 0 ? (
            <div className="replay-metric-grid" data-testid="replay-diff-column">
              {metrics.map((metric) => (
                <MetricCell key={metric.key} metric={metric} />
              ))}
            </div>
          ) : (
            <div className="bundle-empty-state">No metrics available.</div>
          )}
          <div className="bundle-panel" data-testid="tip-pnl-sweep-panel">
            <TipPnlSweepChart
              points={tipPnlSweepPoints}
              activeTipLamports={activeTipLamports}
              calibrationBand={tipPnlCalibrationBand}
            />
          </div>
        </div>
      ) : null}

      {/* Trace */}
      {subtab === "trace" ? (
        <div className="subtab-pane" role="tabpanel">
          {actualResult ? (
            <div data-testid="replay-side-by-side">
              <div className="replay-outcome-grid">
                <div className="replay-outcome-column">
                  <div className="replay-outcome-heading">
                    <span>Actual mainnet</span>
                    <strong className="mono">{actualResult.runId}</strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Decoded coverage</span>
                    <strong>
                      {(actualResult.decodedTransactionShare * 100).toFixed(2)}%
                    </strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Slots loaded</span>
                    <strong>
                      {actualResult.slotsLoaded} /{" "}
                      {actualResult.slotRange[1] - actualResult.slotRange[0] + 1}
                    </strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Counterfactuals</span>
                    <strong>{actualResult.counterfactuals.length}</strong>
                  </div>
                </div>
                <div className="replay-outcome-column">
                  <div className="replay-outcome-heading">
                    <span>Counterfactual</span>
                    <strong className="mono">{result.runId}</strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Decoded coverage</span>
                    <strong>
                      {(result.decodedTransactionShare * 100).toFixed(2)}%
                    </strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Slots loaded</span>
                    <strong>
                      {result.slotsLoaded} /{" "}
                      {result.slotRange[1] - result.slotRange[0] + 1}
                    </strong>
                  </div>
                  <div className="replay-summary-row">
                    <span>Counterfactuals</span>
                    <strong>{result.counterfactuals.length}</strong>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
          <details className="replay-debug-section" open>
            <summary>
              Raw response &amp; last request
              <span> · for debugging</span>
            </summary>
            <div className="bundle-panel" data-testid="replay-service-response">
              <div className="bundle-section-title">Replay response</div>
              <pre className="replay-json-block">
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
            {lastRequest ? (
              <div className="bundle-panel">
                <div className="bundle-section-title">Last request</div>
                <pre className="replay-json-block">
                  {JSON.stringify(lastRequest, null, 2)}
                </pre>
              </div>
            ) : null}
          </details>
        </div>
      ) : null}

      {/* Persistent action row */}
      <div className="replay-result-actions-row">
        <Link
          className="btn btn-primary btn-sm cta-primary"
          href={`/results/${result.runId}`}
          data-testid="replay-open-run-viewer"
        >
          Open in run viewer →
        </Link>
        <button
          className="btn btn-secondary btn-sm"
          type="button"
          disabled={sharePathDisabled}
          onClick={onCopyShareLink}
        >
          Copy share link
        </button>
        <button
          className="btn btn-secondary btn-sm"
          type="button"
          onClick={onReset}
        >
          Reset
        </button>
      </div>
    </div>
  );
}

interface BundleTargetPickerProps {
  bundleId: string;
  targets: ReplayBundleTarget[];
  loading: boolean;
  error: string | null;
  activeSlot: number | null;
  onPick: (target: ReplayBundleTarget) => void;
  onChange: (value: string) => void;
}

function shortenId(id: string): string {
  if (id.length <= 18) return id;
  return `${id.slice(0, 8)}…${id.slice(-6)}`;
}

function BundleTargetPicker({
  bundleId,
  targets,
  loading,
  error,
  activeSlot,
  onPick,
  onChange,
}: BundleTargetPickerProps) {
  const trimmed = bundleId.trim();
  const isEmpty = targets.length === 0;
  const disabled = loading || isEmpty;
  const placeholder = loading
    ? "loading bundles…"
    : error
      ? `error: ${error}`
      : isEmpty
        ? activeSlot !== null
          ? "no bundles in this slot"
          : "load a slot to see bundles"
        : "— pick a bundle —";

  return (
    <div className="replay-field">
      <label htmlFor="replay-tip-bundle-id">Bundle ID</label>
      <select
        id="replay-tip-bundle-id"
        data-testid="replay-tip-bundle-id"
        value={trimmed && targets.some((t) => t.bundleId === trimmed) ? trimmed : ""}
        disabled={disabled}
        onChange={(e) => {
          const value = e.target.value;
          if (!value) {
            onChange("");
            return;
          }
          const picked = targets.find((t) => t.bundleId === value);
          if (picked) onPick(picked);
          else onChange(value);
        }}
      >
        <option value="" disabled={!isEmpty}>
          {placeholder}
        </option>
        {targets.map((t) => (
          <option key={t.bundleId} value={t.bundleId}>
            {shortenId(t.bundleId)} · tip {formatLamports(t.tipLamports)}{" "}
            lamports
          </option>
        ))}
      </select>
      <span />
    </div>
  );
}

interface PoolTargetPickerProps {
  feePool: string;
  targets: ReplayPoolTarget[];
  loading: boolean;
  error: string | null;
  activeSlot: number | null;
  onPick: (target: ReplayPoolTarget) => void;
  onChange: (value: string) => void;
}

function PoolTargetPicker({
  feePool,
  targets,
  loading,
  error,
  activeSlot,
  onPick,
  onChange,
}: PoolTargetPickerProps) {
  const trimmed = feePool.trim();
  const isEmpty = targets.length === 0;
  const disabled = loading || isEmpty;
  const placeholder = loading
    ? "loading pools…"
    : error
      ? `error: ${error}`
      : isEmpty
        ? activeSlot !== null
          ? "no decoded pools in this slot"
          : "load a slot to see pools"
        : "— pick a pool —";

  return (
    <div className="replay-field">
      <label htmlFor="replay-fee-pool">Target pool</label>
      <select
        id="replay-fee-pool"
        data-testid="replay-fee-pool"
        value={trimmed && targets.some((t) => t.poolId === trimmed) ? trimmed : ""}
        disabled={disabled}
        onChange={(e) => {
          const value = e.target.value;
          if (!value) {
            onChange("");
            return;
          }
          const picked = targets.find((t) => t.poolId === value);
          if (picked) onPick(picked);
          else onChange(value);
        }}
      >
        <option value="" disabled={!isEmpty}>
          {placeholder}
        </option>
        {targets.map((t) => (
          <option key={t.poolId} value={t.poolId}>
            {shortenId(t.poolId)} · {t.decodedSwaps} decoded swap
            {t.decodedSwaps === 1 ? "" : "s"}
          </option>
        ))}
      </select>
      <span />
    </div>
  );
}
