"use client";

import { useState, useMemo, useCallback, use, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Card from "@/components/ui/Card";
import StatCard from "@/components/ui/StatCard";
import Tabs from "@/components/ui/Tabs";
import Skeleton from "@/components/feedback/Skeleton";
import { ChartCanvas } from "@/components/charts";
import {
  REPLAY_METRIC_LABELS,
  REPLAY_METRIC_ORDER,
  ReplayMetricsGrid,
  type ReplayMetricKey,
} from "@/components/charts/replay";
import CalibrationBand, {
  type CalibrationBandInput,
} from "@/components/CalibrationBand";
import { type AgentRow, type EvEntry, type SimMetrics, type SimRun } from "@/lib/types";
import { hashColorVar } from "@/lib/utils/hashColor";
import { formatPnl, pnlDenom } from "@/lib/utils/formatPnl";
import {
  simulationService,
  type ExportFormat,
} from "@/lib/services/simulationService";
import {
  agentRowsFromResult,
  chartDataFromResult,
  derivedNumericMetrics,
  metricsFromResult,
  priorityFeeMarketChartFromEvents,
  type ApiRunResult,
  type ChartData,
} from "@/lib/api/adapters/runs";
import RecommendedMetricsGrid from "@/components/results/RecommendedMetricsGrid";
import {
  EMPTY_CALIBRATION_BANDS,
  extractCalibrationBands,
  thresholdForMetric,
} from "@/lib/api/adapters/calibrationBands";
import { useAsync } from "@/lib/hooks/useAsync";
import { ApiError, toToastMessage } from "@/lib/api/errors";
import { API_BASE_URL } from "@/lib/config";
import AgentStoryView from "@/features/results/AgentStoryView";
import WalletArtifactPersistence from "@/components/wallet/WalletArtifactPersistence";
import { useChainIdiom } from "@/lib/hooks/useChainIdiom";
import { useDataTheme } from "@/lib/hooks/useDataTheme";
import { useStudioStore } from "@/lib/state/useStudioStore";

type ResultTab =
  | "summary"
  | "metrics"
  | "charts"
  | "agents"
  | "events"
  | "solana"
  | "exports";

const TAB_ITEMS: { key: ResultTab; label: string }[] = [
  { key: "summary", label: "Summary" },
  { key: "metrics", label: "Metrics" },
  { key: "charts", label: "Charts" },
  { key: "agents", label: "Agents" },
  { key: "events", label: "Events" },
  { key: "solana", label: "Solana" },
  { key: "exports", label: "Exports" },
];

const VALID_TABS = new Set<string>(TAB_ITEMS.map((t) => t.key));

const EVENT_PAGE_SIZE = 200;

function embedUrlForChart(chartId: string, runId: string): string {
  const path = `/embed/${encodeURIComponent(chartId)}?run=${encodeURIComponent(runId)}`;
  if (API_BASE_URL.startsWith("http")) return `${API_BASE_URL}${path}`;
  if (typeof window !== "undefined") {
    return new URL(`${API_BASE_URL}${path}`, window.location.href).toString();
  }
  return `${API_BASE_URL}${path}`;
}

function escapeAttribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
}

interface ResultsBundle {
  run: SimRun | undefined;
  result: ApiRunResult | null;
  metrics: SimMetrics;
  // Engine-emitted ``metadata.derived_metrics`` filtered to finite numerics
  // (plus ±Infinity, which ``fees_vs_il_breakeven`` uses as a sentinel).
  // Keys may carry a ``:agent_id`` suffix for per-LP variants.
  derivedMetrics: Record<string, number>;
  agents: AgentRow[];
  spec: unknown;
  resultState: "ready" | "pending" | "missing";
  resultMessage?: string;
}

const EMPTY_METRICS: SimMetrics = {
  klDivergence: null,
  convergenceSpeed: null,
  lpProfitability: null,
  manipulationCost: null,
  maxDrawdown: 0,
  rollingVol: 0,
  twap: 0,
  slippage: null,
  exitability: null,
  compositeScore: 0,
  stressScore: 0,
  sandwichBundlesLanded: 0,
  sandwichBundlesSubmitted: 0,
  sandwichRealizedEvLamports: 0,
  tickCrossings: 0,
};

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ResultsPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const { showToast } = useToast();

  // ── URL-driven tab state ──────────────────────────────
  const tabParam = searchParams.get("tab") ?? "summary";
  const activeTab: ResultTab = VALID_TABS.has(tabParam) ? (tabParam as ResultTab) : "summary";
  const sharedMode = searchParams.get("shared") === "1";

  const setActiveTab = useCallback(
    (tab: ResultTab) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("tab", tab);
      router.replace(`/results/${runId}?${params.toString()}`, { scroll: false });
    },
    [router, runId, searchParams],
  );

  // ── Bundle: load run + result + spec in parallel ──────
  const bundle = useAsync<ResultsBundle>(
    async () => {
      if (sharedMode) {
        const shared = await simulationService.getSharedRunBundle(runId);
        if (!shared.result) {
          return {
            run: shared.run,
            result: null,
            metrics: EMPTY_METRICS,
            derivedMetrics: {},
            agents: [],
            spec: shared.spec,
            resultState: "missing" as const,
            resultMessage: "The final result artifact for this shared run is not available.",
          };
        }
        return {
          run: shared.run,
          result: shared.result,
          metrics: metricsFromResult(shared.result),
          derivedMetrics: derivedNumericMetrics(shared.result),
          agents: agentRowsFromResult(shared.result),
          spec: shared.spec,
          resultState: "ready" as const,
        };
      }

      const [run, spec] = await Promise.all([
        simulationService.getRun(runId),
        simulationService.getSpec(runId).catch(() => null),
      ]);
      if (!run) {
        return {
          run,
          result: null,
          metrics: EMPTY_METRICS,
          derivedMetrics: {},
          agents: [],
          spec,
          resultState: "missing" as const,
          resultMessage: "Run not found.",
        };
      }

      try {
        const result = await simulationService.getResult(runId);
        return {
          run,
          result,
          metrics: metricsFromResult(result),
          derivedMetrics: derivedNumericMetrics(result),
          agents: agentRowsFromResult(result),
          spec,
          resultState: "ready" as const,
        };
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          const pending = run.status !== "completed";
          return {
            run,
            result: null,
            metrics: EMPTY_METRICS,
            derivedMetrics: {},
            agents: [],
            spec,
            resultState: pending ? ("pending" as const) : ("missing" as const),
            resultMessage: pending
              ? `This run is ${run.status}. Final analytics are available after completion.`
              : "The final result artifact for this run is not available.",
          };
        }
        throw err;
      }
    },
    [runId, sharedMode],
  );

  // ── Available runs fallback (when current runId is missing) ──
  const runsList = useAsync<SimRun[]>(
    () => (sharedMode ? Promise.resolve([]) : simulationService.listRuns()),
    [runId, sharedMode],
  );

  // ── Chain idiom (per-spec time-unit vocabulary) ──────────────
  const idiom = useChainIdiom(bundle.data?.run?.spec ?? null);
  useDataTheme(bundle.data?.run?.spec ?? null);

  // ── Live chrome (Solana slot ticker) ─────────────────────────
  // The results page is read-only — there's no /step driver to pump
  // applyStep() — so seed liveSlot/liveLeader directly from the latest
  // round_snapshot that carries Solana slot metadata. Snapshots from
  // non-Solana runs leave current_slot null and the ticker stays hidden.
  const { setLiveSlot, setLiveLeader } = useStudioStore();
  useEffect(() => {
    const snapshots = bundle.data?.result?.round_snapshots;
    if (!snapshots || snapshots.length === 0) return;
    let lastSlot: number | null = null;
    let lastLeader: string | null = null;
    for (const snap of snapshots) {
      const slot = (snap as { current_slot?: unknown }).current_slot;
      const leader = (snap as { current_leader?: unknown }).current_leader;
      if (typeof slot === "number") lastSlot = slot;
      if (typeof leader === "string") lastLeader = leader;
    }
    if (lastSlot !== null) setLiveSlot(lastSlot);
    setLiveLeader(lastLeader);
  }, [bundle.data, setLiveSlot, setLiveLeader]);

  // ── Local state for filters and modals ────────────────
  const [agentRoleFilter, setAgentRoleFilter] = useState("all");
  const [sortBy, setSortBy] = useState("pnl");
  const [selectedAgent, setSelectedAgent] = useState<AgentRow | null>(null);

  // ── Events pagination ─────────────────────────────────
  const [events, setEvents] = useState<EvEntry[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsExhausted, setEventsExhausted] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const loadMoreEvents = useCallback(async () => {
    if (eventsLoading || eventsExhausted) return;
    setEventsLoading(true);
    setEventsError(null);
    try {
      const offset = events.length;
      const page = await simulationService.getEvents(runId, {
        limit: EVENT_PAGE_SIZE,
        offset,
      });
      setEvents((prev) => (offset === 0 ? page : [...prev, ...page]));
      setEventsExhausted(page.length < EVENT_PAGE_SIZE);
    } catch (err) {
      setEventsError(toToastMessage(err));
    } finally {
      setEventsLoading(false);
    }
  }, [runId, events.length, eventsLoading, eventsExhausted]);

  // Auto-load on first time the events tab (or solana tab — which derives
  // bundle / fee-market / jito counts from the same event log) is opened.
  useEffect(() => {
    if (
      (activeTab === "events" || activeTab === "solana") &&
      events.length === 0 &&
      !eventsLoading &&
      !eventsExhausted
    ) {
      loadMoreEvents();
    }
  }, [activeTab, events.length, eventsLoading, eventsExhausted, loadMoreEvents]);

  // Reset pagination when navigating to a new run.
  useEffect(() => {
    setEvents([]);
    setEventsExhausted(false);
    setEventsError(null);
    // Reset live-chrome too so a previous run's slot doesn't leak into
    // the new run before its snapshots arrive.
    setLiveSlot(0);
    setLiveLeader(null);
  }, [runId, setLiveSlot, setLiveLeader]);

  // ── Filtered + sorted agents ──────────────────────────
  const filteredAgents = useMemo(() => {
    const list = bundle.data?.agents ?? [];
    const filtered =
      agentRoleFilter === "all" ? list : list.filter((a) => a.role === agentRoleFilter);
    return [...filtered].sort((a, b) => {
      if (sortBy === "pnl") return b.pnl - a.pnl;
      if (sortBy === "volume") return b.volume - a.volume;
      if (sortBy === "balance") return b.balance - a.balance;
      return 0;
    });
  }, [bundle.data?.agents, agentRoleFilter, sortBy]);

  // ── Dynamic role filter options (US-015) ──────────────
  // Derive filter options from observed agents rather than a fixed
  // role list, so backend-defined agent types that the frontend does
  // not know about still appear in the dropdown.
  const roleFilterOptions = useMemo(() => {
    const list = bundle.data?.agents ?? [];
    const seen = new Set<string>();
    for (const a of list) {
      if (typeof a.role === "string" && a.role.length > 0) seen.add(a.role);
    }
    return [...seen].sort();
  }, [bundle.data?.agents]);

  // ── Solana run detector (lighthouse / sandwich / etc.) ─
  const isSolanaRun =
    bundle.data?.run?.spec.execution.model === "solana" ||
    bundle.data?.run?.spec.execution.model === "solana_like";

  const market = bundle.data?.run?.spec.market;
  const bundleResult = bundle.data?.result ?? null;
  const denomLabel = pnlDenom(market, bundleResult);

  // ── World-market selector ─────────────────────────────
  const isWorldRun = bundle.data?.run?.market.toLowerCase().includes("world") ?? false;
  const marketParam = searchParams.get("market") ?? "all";

  const setSelectedMarket = useCallback(
    (market: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("market", market);
      router.replace(`/results/${runId}?${params.toString()}`, { scroll: false });
    },
    [router, runId, searchParams],
  );

  const worldMarketOptions = useMemo(() => {
    const opts: { key: string; label: string }[] = [{ key: "all", label: "All Markets" }];
    if (isWorldRun) {
      const spec = bundle.data?.spec as
        | { market?: { markets?: Record<string, unknown> } }
        | null
        | undefined;
      const names = spec?.market?.markets ? Object.keys(spec.market.markets) : [];
      for (const name of names) opts.push({ key: name, label: name });
    }
    return opts;
  }, [isWorldRun, bundle.data?.spec]);

  // ── Exports ───────────────────────────────────────────
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const handleExport = useCallback(
    async (format: ExportFormat) => {
      if (exporting) return;
      setExporting(format);
      try {
        const blob = await simulationService.exportResult(runId, format);
        const ext = format === "parquet" ? "parquet" : format;
        triggerBlobDownload(blob, `${runId}.${ext}`);
        showToast(`Exported ${runId}.${ext}`, "success");
      } catch (err) {
        showToast(`Export failed: ${toToastMessage(err)}`, "error");
      } finally {
        setExporting(null);
      }
    },
    [runId, showToast, exporting],
  );

  const embedCodeForChart = useCallback(
    (chartId: string, title: string) => {
      const src = embedUrlForChart(chartId, runId);
      return `<iframe src="${escapeAttribute(src)}" title="${escapeAttribute(title)}" loading="lazy" width="720" height="440" style="border:0;max-width:100%;" referrerpolicy="no-referrer"></iframe>`;
    },
    [runId],
  );

  const handleCopyEmbedCode = useCallback(
    async (chartId: string, title: string) => {
      try {
        if (!navigator.clipboard?.writeText) {
          throw new Error("Clipboard API unavailable.");
        }
        await navigator.clipboard.writeText(embedCodeForChart(chartId, title));
        showToast("Embed code copied", "success");
      } catch {
        showToast("Copy unavailable in this browser", "error");
      }
    },
    [embedCodeForChart, showToast],
  );

  const embedButton = useCallback(
    (chartId: string, title: string) => (
      <button
        className="btn btn-secondary btn-sm"
        data-testid={`copy-embed-${chartId}`}
        type="button"
        onClick={() => handleCopyEmbedCode(chartId, title)}
      >
        Copy embed
      </button>
    ),
    [handleCopyEmbedCode],
  );

  // PRD US-010 line 748: per-account fee-market chart on the results page.
  // Lines = percentile × hot account, derived from the
  // PRIORITY_FEE_MARKET_UPDATED event stream.
  const priorityFeeMarketChart = useMemo(
    () => priorityFeeMarketChartFromEvents(events),
    [events],
  );

  const charts: ChartData = useMemo(
    () =>
      bundle.data?.resultState === "ready" && bundle.data.result
        ? chartDataFromResult(bundle.data.result, {
            market: isWorldRun && marketParam !== "all" ? marketParam : undefined,
          })
        : {
            priceData: [],
            priceLabels: [],
            cumVol: [],
            liq: [],
            fees: [],
            feesByDestination: [],
            pnlData: [],
            pnlColors: [],
            tickCrossings: [],
            activeLiquidity: [],
            totalLpLiquidity: [],
            baselineLpLiquidity: [],
            agentLpLiquidity: [],
            feesA: [],
            feesB: [],
          },
    [bundle.data, isWorldRun, marketParam],
  );

  const replayMetrics = useMemo(() => {
    if (bundle.data?.resultState !== "ready" || !bundle.data.result) return null;
    const snapshots = bundle.data.result.round_snapshots ?? [];
    for (let i = snapshots.length - 1; i >= 0; i--) {
      const replay = (snapshots[i] as { metrics?: { replay?: unknown } }).metrics?.replay;
      if (replay && typeof replay === "object") return replay;
    }
    return null;
  }, [bundle.data]);

  // PRD US-004 line 781: every chart on the results page shows a calibration
  // band when the underlying run carries a `mainnet_accuracy_claim`. Bands
  // come from `result.replay_diff` (per-metric ErrorBand map), thresholds
  // from `solana-plans/calibration/thresholds.yaml` (mirrored in TS).
  const isCalibratedReplay =
    bundle.data?.run?.calibration?.isCalibratedReplay === true;
  const calibrationBands = useMemo(
    () =>
      bundle.data?.resultState === "ready" && bundle.data.result
        ? extractCalibrationBands(bundle.data.result)
        : EMPTY_CALIBRATION_BANDS,
    [bundle.data],
  );
  const bandInputForFamily = useCallback(
    (family: string) => {
      const band = calibrationBands.family[family];
      if (!band) {
        return {
          isCalibratedReplay,
          predicted: null,
          actual: null,
          supported: false,
          threshold: thresholdForMetric(family),
        };
      }
      return {
        isCalibratedReplay,
        predicted: band.predicted,
        actual: band.actual,
        supported: band.supported ?? band.actual !== null,
        threshold: thresholdForMetric(band.metric) ?? thresholdForMetric(family),
      };
    },
    [calibrationBands, isCalibratedReplay],
  );
  const bandInputForMetric = useCallback(
    (metricKey: string): CalibrationBandInput => {
      const band = calibrationBands.byMetric[metricKey];
      if (!band) {
        return {
          isCalibratedReplay,
          predicted: null,
          actual: null,
          supported: false,
          threshold: thresholdForMetric(metricKey),
        };
      }
      return {
        isCalibratedReplay,
        predicted: band.predicted,
        actual: band.actual,
        supported: band.supported ?? band.actual !== null,
        threshold: thresholdForMetric(band.metric) ?? thresholdForMetric(metricKey),
      };
    },
    [calibrationBands, isCalibratedReplay],
  );
  const replayMetricCalibrationBands = useMemo(() => {
    const bands: Partial<Record<ReplayMetricKey, CalibrationBandInput>> = {};
    for (const key of REPLAY_METRIC_ORDER) {
      bands[key] = bandInputForMetric(key);
    }
    return bands;
  }, [bandInputForMetric]);

  // ── Render: error / loading guards ────────────────────
  if (bundle.loading) {
    return (
      <>
        <Topbar title="Results & Analytics" spec={bundle.data?.run?.spec ?? null} />
        <div id="content" className="fade-in">
          <Card title="Loading run">
            <Skeleton height={20} width="40%" />
            <div style={{ marginTop: 12 }}>
              <Skeleton height={12} />
            </div>
            <div style={{ marginTop: 6 }}>
              <Skeleton height={12} width="80%" />
            </div>
          </Card>
        </div>
      </>
    );
  }

  if (bundle.error || !bundle.data) {
    const msg =
      bundle.error instanceof ApiError && bundle.error.status === 404
        ? "Run not found."
        : toToastMessage(bundle.error);
    return (
      <>
        <Topbar title="Results & Analytics" spec={bundle.data?.run?.spec ?? null} />
        <div id="content" className="fade-in">
          <Card title="Failed to load results">
            <p style={{ color: "var(--red)", fontSize: ".88rem" }}>{msg}</p>
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-secondary" onClick={() => router.push("/dashboard")}>
                Back to Dashboard
              </button>
            </div>
          </Card>
        </div>
      </>
    );
  }

  if (bundle.data.resultState !== "ready" || !bundle.data.result) {
    const { run } = bundle.data;
    const isRunnable = run?.status === "running" || run?.status === "paused";
    const showPicker = !run;
    const otherRuns = (runsList.data ?? []).filter((r) => r.id !== runId);
    return (
      <>
        <Topbar title="Results & Analytics" spec={bundle.data?.run?.spec ?? null} />
        <div id="content" className="fade-in">
          <Card title={bundle.data.resultState === "pending" ? "Results not ready" : "Results unavailable"}>
            <p style={{ color: "var(--text-2)", fontSize: ".88rem" }}>
              {bundle.data.resultMessage || "Final results are not available for this run."}
            </p>
            <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              {isRunnable && run ? (
                <button className="btn btn-secondary" onClick={() => router.push(`/runner/${run.id}`)}>
                  Open Runner
                </button>
              ) : null}
              <button className="btn btn-secondary" onClick={bundle.refetch}>
                Retry
              </button>
              <button className="btn btn-secondary" onClick={() => router.push("/dashboard")}>
                Back to Dashboard
              </button>
            </div>
          </Card>

          {showPicker && (
            <Card title="Pick an available run">
              {runsList.loading && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <Skeleton height={20} />
                  <Skeleton height={20} />
                  <Skeleton height={20} />
                </div>
              )}
              {!runsList.loading && runsList.error != null && (
                <p style={{ color: "var(--red)", fontSize: ".85rem" }}>
                  Failed to load runs: {toToastMessage(runsList.error)}
                </p>
              )}
              {!runsList.loading && runsList.error == null && otherRuns.length === 0 && (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  No runs recorded yet. Build a simulation to get started.
                </p>
              )}
              {!runsList.loading && otherRuns.length > 0 && (
                <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Run</th>
                        <th>Market</th>
                        <th>Status</th>
                        <th>{idiom.rounds_label}</th>
                        <th>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {otherRuns.map((r) => (
                        <tr
                          key={r.id}
                          style={{ cursor: "pointer" }}
                          onClick={() => router.push(`/results/${r.id}`)}
                        >
                          <td className="mono" style={{ color: "var(--accent)" }}>{r.id}</td>
                          <td>{r.market}</td>
                          <td>{r.status}</td>
                          <td className="mono">
                            {r.currentRound}
                            {r.totalRounds ? ` / ${r.totalRounds}` : ""}
                          </td>
                          <td style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                            {new Date(r.createdAt).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
        </div>
      </>
    );
  }

  const { run, result, metrics, derivedMetrics } = bundle.data;
  const numRoundsExecuted =
    (typeof result.num_rounds_executed === "number" && result.num_rounds_executed) ||
    (typeof result.num_rounds === "number" && result.num_rounds) ||
    run?.totalRounds ||
    0;
  const compositeScore = metrics.compositeScore;

  // PRD US-011 line 891-894: bundle outcomes live on each RoundSnapshot
  // (status: landed | reverted | dropped). The engine emits BUNDLE_TIP_PAID
  // on land but does not emit "bundle_landed"/"bundle_reverted" event types,
  // so derive the studio-tab counts directly from the snapshot ledger.
  // Also tally validator + stake-pool revenue per outcome so the
  // run page can show a real tips-paid total alongside the counts.
  const {
    bundleOutcomeCounts,
    tipsPaidTotalLamports,
    bundleOutcomeTimeline,
    bundleLandingRateStats,
    dropReasonCounts,
  } = (() => {
    let landed = 0;
    let reverted = 0;
    let dropped = 0;
    let tipsPaid = 0;
    const landedByRound: number[] = [];
    const revertedByRound: number[] = [];
    const droppedByRound: number[] = [];
    const perRoundLandingRates: number[] = [];
    const dropReasons: Record<string, number> = {};
    for (const snap of result.round_snapshots ?? []) {
      const outcomes = (snap as { bundle_outcomes?: unknown }).bundle_outcomes;
      let rl = 0;
      let rr = 0;
      let rd = 0;
      if (Array.isArray(outcomes)) {
        for (const outcome of outcomes) {
          const status = (outcome as { status?: unknown }).status;
          if (status === "landed") rl += 1;
          else if (status === "reverted") rr += 1;
          else if (status === "dropped") {
            rd += 1;
            const reason = (outcome as { drop_reason?: unknown }).drop_reason;
            const key = typeof reason === "string" && reason ? reason : "unknown";
            dropReasons[key] = (dropReasons[key] ?? 0) + 1;
          }
          if (status === "landed") {
            const validatorRev = Number(
              (outcome as { validator_revenue_lamports?: unknown })
                .validator_revenue_lamports ?? 0,
            );
            const stakePoolRev = Number(
              (outcome as { stake_pool_revenue_lamports?: unknown })
                .stake_pool_revenue_lamports ?? 0,
            );
            if (Number.isFinite(validatorRev)) tipsPaid += validatorRev;
            if (Number.isFinite(stakePoolRev)) tipsPaid += stakePoolRev;
          }
        }
      }
      landed += rl;
      reverted += rr;
      dropped += rd;
      landedByRound.push(rl);
      revertedByRound.push(rr);
      droppedByRound.push(rd);
      const total = rl + rr + rd;
      if (total > 0) perRoundLandingRates.push(rl / total);
    }
    let avgLandingRate = 0;
    let stdevLandingRate = 0;
    if (perRoundLandingRates.length > 0) {
      avgLandingRate =
        perRoundLandingRates.reduce((s, v) => s + v, 0) /
        perRoundLandingRates.length;
      const variance =
        perRoundLandingRates.reduce(
          (s, v) => s + (v - avgLandingRate) ** 2,
          0,
        ) / perRoundLandingRates.length;
      stdevLandingRate = Math.sqrt(variance);
    }
    return {
      bundleOutcomeCounts: { landed, reverted, dropped },
      tipsPaidTotalLamports: tipsPaid,
      bundleOutcomeTimeline: {
        landed: landedByRound,
        reverted: revertedByRound,
        dropped: droppedByRound,
      },
      bundleLandingRateStats: {
        avg: avgLandingRate,
        stdev: stdevLandingRate,
        roundsWithBundles: perRoundLandingRates.length,
      },
      dropReasonCounts: dropReasons,
    };
  })();

  // PRD US-013 line 1053: JitoSearcher exposes its per-strategy counters
  // under metrics.jito_searcher.<agent_id>. The final snapshot's payload
  // is the canonical surface for landing-rate / tip-ROI on the run page;
  // we sum across strategies when more than one is configured.
  //
  // FIX-020: when the bound BundleAuction carries a fitted TipQuoteCurve,
  // the searcher's payload swaps `synthetic: true` for a `calibration`
  // metadata block ({source, captured_at, n_bundles, n_slots,
  // landing_rate}). We surface that block as a quiet footer and drop the
  // "uncalibrated landing rate" badge.
  type CalibrationMeta = {
    source?: unknown;
    captured_at?: unknown;
    n_bundles?: unknown;
    n_slots?: unknown;
    n_in_cohort?: unknown;
    landing_rate?: unknown;
  };
  const jitoSearcherSummary = (() => {
    const snapshots = result.round_snapshots ?? [];
    const last = snapshots[snapshots.length - 1] as
      | { metrics?: { jito_searcher?: Record<string, unknown> } }
      | undefined;
    const payload = last?.metrics?.jito_searcher;
    if (!payload || typeof payload !== "object") {
      return null;
    }
    let bundlesSubmitted = 0;
    let bundlesLanded = 0;
    let tipsSubmitted = 0;
    let tipsPaid = 0;
    let realizedEv = 0;
    let synthetic = false;
    let calibration: CalibrationMeta | null = null;
    let strategyCount = 0;
    for (const searcherPayload of Object.values(payload)) {
      if (!searcherPayload || typeof searcherPayload !== "object") continue;
      const sp = searcherPayload as {
        synthetic?: unknown;
        calibration?: CalibrationMeta;
        by_strategy?: Record<string, unknown>;
      };
      if (sp.synthetic === true) synthetic = true;
      if (sp.calibration && typeof sp.calibration === "object") {
        // Multiple searchers in one run share the auction's calibration,
        // so the first non-null block is canonical.
        calibration = calibration ?? sp.calibration;
      }
      const byStrategy = sp.by_strategy;
      if (!byStrategy || typeof byStrategy !== "object") continue;
      for (const counters of Object.values(byStrategy)) {
        if (!counters || typeof counters !== "object") continue;
        const c = counters as Record<string, unknown>;
        bundlesSubmitted += Number(c.bundles_submitted ?? 0) || 0;
        bundlesLanded += Number(c.bundles_landed ?? 0) || 0;
        tipsSubmitted += Number(c.tips_submitted_lamports ?? 0) || 0;
        tipsPaid += Number(c.tips_paid_lamports ?? 0) || 0;
        realizedEv += Number(c.realized_ev_lamports ?? 0) || 0;
        strategyCount += 1;
      }
    }
    if (strategyCount === 0) return null;
    const landingRate =
      bundlesSubmitted > 0 ? bundlesLanded / bundlesSubmitted : 0;
    const tipRoi = tipsPaid > 0 ? realizedEv / tipsPaid : 0;
    return {
      bundlesSubmitted,
      bundlesLanded,
      tipsSubmitted,
      tipsPaid,
      realizedEv,
      landingRate,
      tipRoi,
      synthetic,
      calibration,
    };
  })();

  return (
    <>
      <Topbar title="Results & Analytics" spec={bundle.data?.run?.spec ?? null} />

      <div id="content" className="fade-in">
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <p style={{ color: "var(--text-2)", fontSize: ".82rem", margin: 0 }}>
            Run <span className="mono" style={{ color: "var(--accent)" }}>{runId}</span>
            {run ? (
              <>
                {" "}
                · <span className="mono">{run.market}</span> · seed{" "}
                <span className="mono">{run.seed}</span> · {numRoundsExecuted} {idiom.rounds_label.toLowerCase()}
              </>
            ) : null}
          </p>
          {isWorldRun && worldMarketOptions.length > 1 && (
            <select
              data-testid="results-market-select"
              value={marketParam}
              onChange={(e) => setSelectedMarket(e.target.value)}
              style={{ fontSize: ".82rem" }}
            >
              {worldMarketOptions.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          )}
        </div>

        <Tabs
          items={TAB_ITEMS.filter((t) => t.key !== "solana" || isSolanaRun)}
          active={activeTab}
          onChange={setActiveTab}
        />

        {/* ── Summary Tab ─────────────────────────────────── */}
        {activeTab === "summary" && (
          <div className="tab-panel active">
            <WalletArtifactPersistence runId={runId} />

            {metrics.sandwichRealizedEvLamports > 0 && (
              <div
                className="stat-card"
                data-testid="mev-extracted-hero"
                onClick={() => setActiveTab("agents")}
                style={{
                  cursor: "pointer",
                  marginBottom: 20,
                  padding: "20px 24px",
                  borderLeft: "3px solid var(--red)",
                }}
              >
                <span className="label" style={{ fontSize: ".78rem" }}>
                  MEV extracted from victims
                </span>
                <span
                  className="value"
                  style={{
                    fontSize: "2.4rem",
                    fontFamily: "var(--font-mono)",
                    color: "var(--red)",
                  }}
                >
                  {(metrics.sandwichRealizedEvLamports / 1e9).toFixed(4)} SOL
                </span>
                <span className="hint">
                  {metrics.sandwichRealizedEvLamports.toLocaleString()} lamports
                  · {metrics.sandwichBundlesLanded}/
                  {metrics.sandwichBundlesSubmitted} sandwich bundles landed
                  {jitoSearcherSummary?.synthetic ? (
                    <>
                      {" · "}
                      <span
                        className="synthetic-badge"
                        title="Bundle mechanics and pool state are real. The probability of any given bundle landing is illustrative until calibrated against on-chain Jito data (Phase 2.4)."
                        style={{ verticalAlign: "middle" }}
                      >
                        ⚠ synthetic landing rate
                      </span>
                    </>
                  ) : null}
                </span>
              </div>
            )}

            {/* Engine-emitted derived metrics + client-derived essentials,
                rendered in a single ``grid-4`` so the trailing client tiles
                fill the dynamic block's last row instead of starting a new
                one. Engine tiles come from ``metadata.derived_metrics`` and
                vary per template; the client tiles (LP fee yield, tick
                crossings, drawdown, vol) are derived in ``runs.ts`` from
                price/liquidity/round_snapshots and always render. */}
            <RecommendedMetricsGrid
              metrics={derivedMetrics}
              emptyHint="No engine-derived metrics emitted for this template."
              trailing={
                <>
                  <StatCard
                    label="LP fee yield"
                    value={
                      metrics.lpProfitability !== null
                        ? metrics.lpProfitability.toFixed(3)
                        : "—"
                    }
                    valueColor={
                      metrics.lpProfitability !== null && metrics.lpProfitability > 1
                        ? "var(--green)"
                        : undefined
                    }
                    hint="1 + fee yield over the run"
                  />
                  <StatCard
                    label="Tick crossings"
                    value={metrics.tickCrossings.toLocaleString()}
                    hint="Initialized ticks consumed by swaps"
                  />
                  <StatCard
                    label="Max drawdown (price)"
                    value={`${metrics.maxDrawdown.toFixed(2)}%`}
                    valueColor={metrics.maxDrawdown < -5 ? "var(--red)" : undefined}
                    hint="Worst peak-to-trough on the spot price"
                  />
                  <StatCard
                    label="Rolling volatility"
                    value={metrics.rollingVol.toFixed(4)}
                    hint="20-round window"
                  />
                </>
              }
            />


            <div className="grid-2">
              <Card title="Composite Score">
                <div style={{ textAlign: "center", padding: "20px 0" }}>
                  <div
                    style={{
                      fontSize: "3rem",
                      fontWeight: 700,
                      fontFamily: "var(--font-mono)",
                      color: "var(--green)",
                    }}
                  >
                    {compositeScore}
                  </div>
                  <p style={{ color: "var(--text-2)", fontSize: ".85rem", marginTop: 8 }}>
                    Heuristic blend of drawdown, volatility, and LP profitability
                  </p>
                </div>
              </Card>

              <Card title="Manipulation Stress">
                <div style={{ textAlign: "center", padding: "20px 0" }}>
                  <div
                    style={{
                      fontSize: "3rem",
                      fontWeight: 700,
                      fontFamily: "var(--font-mono)",
                      color:
                        metrics.stressScore >= 50
                          ? "var(--red)"
                          : metrics.stressScore >= 20
                            ? "var(--yellow, #d4a017)"
                            : "var(--green)",
                    }}
                  >
                    {metrics.stressScore}
                  </div>
                  <p style={{ color: "var(--text-2)", fontSize: ".85rem", marginTop: 8 }}>
                    {metrics.sandwichBundlesLanded > 0
                      ? `${metrics.sandwichBundlesLanded}/${metrics.sandwichBundlesSubmitted} bundles landed · ${(metrics.sandwichRealizedEvLamports / 1e9).toFixed(4)} SOL extracted`
                      : "No successful sandwich attacks"}
                  </p>
                </div>
              </Card>
            </div>
          </div>
        )}

        {/* ── Metrics Tab ─────────────────────────────────── */}
        {activeTab === "metrics" && (
          <div className="tab-panel active">
            <Card title="Computed Metrics">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Value</th>
                      <th>Direction</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Max Drawdown</td>
                      <td className="mono" style={{ color: "var(--red)" }}>
                        {metrics.maxDrawdown.toFixed(2)}%
                      </td>
                      <td>Lower &#10003;</td>
                    </tr>
                    <tr>
                      <td>Rolling Volatility (20)</td>
                      <td className="mono">{metrics.rollingVol.toFixed(4)}</td>
                      <td>—</td>
                    </tr>
                    <tr>
                      <td>TWAP</td>
                      <td className="mono">{metrics.twap.toFixed(4)}</td>
                      <td>—</td>
                    </tr>
                    <tr>
                      <td>LP Profitability</td>
                      <td className="mono">
                        {metrics.lpProfitability !== null
                          ? metrics.lpProfitability.toFixed(4)
                          : "—"}
                      </td>
                      <td>Higher &#10003;</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}

        {/* ── Charts Tab ──────────────────────────────────── */}
        {activeTab === "charts" && (
          <div className="tab-panel active">
            <div className="grid-2">
              <Card title="Price Series" actions={embedButton("price-series", "Price Series")}>
                {charts.priceData.length === 0 ? (
                  <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No price data.</p>
                ) : (
                  <ChartCanvas
                    height={240}
                    datasets={charts.priceData.map((data, i) => ({
                      data,
                      color: ["#6c8aff", "#34d399", "#fbbf24", "#a78bfa", "#22d3ee", "#f472b6"][
                        i % 6
                      ],
                      label: charts.priceLabels[i] ?? `Series ${i + 1}`,
                      fill: i === 0,
                    }))}
                  />
                )}
                <CalibrationBand
                  input={bandInputForFamily("pool_price")}
                  metricLabel="Pool price"
                />
              </Card>
              <Card
                title="Cumulative Volume"
                actions={embedButton("cumulative-volume", "Cumulative Volume")}
              >
                {charts.cumVol.length === 0 ? (
                  <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No volume data.</p>
                ) : (
                  <ChartCanvas
                    height={240}
                    decimals={0}
                    datasets={[
                      { data: charts.cumVol, color: "#6c8aff", label: "Cumulative Volume", fill: true },
                    ]}
                  />
                )}
                <CalibrationBand
                  input={bandInputForFamily("total_volume")}
                  metricLabel="Total volume"
                />
              </Card>
              <Card
                title="Total LP Deposits Over Time"
                actions={embedButton("total-lp-deposits", "Total LP Deposits Over Time")}
              >
                {charts.liq.length === 0 ? (
                  <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No liquidity data.</p>
                ) : charts.totalLpLiquidity.length > 0 ? (
                  (() => {
                    const baselineMax = Math.max(...charts.baselineLpLiquidity, 0);
                    const agent = charts.agentLpLiquidity;
                    const agentMin = Math.min(...agent);
                    const agentMax = Math.max(...agent);
                    const agentMoved = agentMax !== agentMin || agentMax !== 0;
                    // Count mint/burn events: any round-over-round change in
                    // total deposited L is a mint or a burn. (Diffs of size
                    // 1 raw unit can come from fixed-point rounding on a
                    // round-trip — ignore those to keep the count honest.)
                    let events = 0;
                    for (let i = 1; i < agent.length; i++) {
                      if (Math.abs(agent[i] - agent[i - 1]) > 1) events += 1;
                    }
                    const fmt = (n: number) =>
                      n.toLocaleString(undefined, { maximumFractionDigits: 0 });
                    return (
                      <>
                        <ChartCanvas
                          height={240}
                          decimals={0}
                          datasets={[
                            {
                              data: agent,
                              color: "#a78bfa",
                              label: "Sim agent L",
                              fill: true,
                            },
                          ]}
                        />
                        <div
                          style={{
                            marginTop: 10,
                            display: "flex",
                            flexWrap: "wrap",
                            gap: "8px 16px",
                            color: "var(--text-2)",
                            fontSize: ".78rem",
                          }}
                        >
                          <span>
                            Mint / burn events: <strong>{events}</strong>
                          </span>
                          <span>
                            Agent L range: {fmt(agentMin)} → {fmt(agentMax)}
                          </span>
                          <span>Chain baseline (constant): {fmt(baselineMax)}</span>
                        </div>
                        <p
                          style={{
                            color: "var(--text-2)",
                            fontSize: ".78rem",
                            marginTop: 10,
                          }}
                        >
                          Net L added or removed by simulated LP agents
                          during the run, on its own scale. Steps only on
                          mint / burn — flat regions = no LP activity, not a
                          bug. The chain-hydrated baseline (forks: real
                          on-chain L at run start) sits underneath as a
                          constant context value, shown numerically above.
                        </p>
                        {!agentMoved && (
                          <p
                            style={{
                              color: "var(--warn, #d97706)",
                              fontSize: ".78rem",
                              marginTop: 8,
                            }}
                          >
                            No simulated LP mints or burns recorded — the
                            series is identically zero across all rounds. If
                            you expected activity, confirm your spec
                            includes a ``passive_lp`` / ``rebalancing_lp``
                            seeded with both pool tokens, and that the
                            deposit isn't being silently dropped (Solana
                            ``submission_path_drop`` priors).
                          </p>
                        )}
                      </>
                    );
                  })()
                ) : (
                  <ChartCanvas
                    height={240}
                    decimals={0}
                    datasets={[
                      { data: charts.liq, color: "#a78bfa", label: "Total LP L", fill: true },
                    ]}
                  />
                )}
                <CalibrationBand
                  input={bandInputForFamily("lp_balance")}
                  metricLabel="LP balance"
                />
              </Card>
              <Card
                title="Cumulative Fees"
                actions={embedButton("cumulative-fees", "Cumulative Fees")}
              >
                {charts.fees.length === 0 ? (
                  <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No fee data.</p>
                ) : charts.feesA.length > 0 &&
                  (charts.feesA.at(-1) ?? 0) + (charts.feesB.at(-1) ?? 0) > 0 ? (
                  <>
                    <ChartCanvas
                      height={240}
                      decimals={0}
                      stacked
                      datasets={[
                        {
                          data: charts.feesA,
                          color: "#34d399",
                          label: "Fees in token A",
                          fill: true,
                        },
                        {
                          data: charts.feesB,
                          color: "#6c8aff",
                          label: "Fees in token B",
                          fill: true,
                        },
                      ]}
                    />
                    <p
                      style={{
                        color: "var(--text-2)",
                        fontSize: ".78rem",
                        marginTop: 8,
                      }}
                    >
                      LP fees split by which side of the pair was the
                      input token (fees are taken in token-in). Heavy skew
                      toward one side = directional flow; balanced = mean
                      reverting.
                    </p>
                  </>
                ) : (
                  <ChartCanvas
                    height={240}
                    decimals={0}
                    datasets={[
                      { data: charts.fees, color: "#34d399", label: "Cumulative Fees", fill: true },
                    ]}
                  />
                )}
                <CalibrationBand
                  input={bandInputForFamily("tips_paid")}
                  metricLabel="Tips paid (lamports)"
                />
              </Card>
              {charts.tickCrossings.length > 0 && (
                <Card
                  title="Tick Activity"
                  actions={embedButton("tick-activity", "Tick Activity")}
                >
                  <ChartCanvas
                    height={240}
                    decimals={0}
                    datasets={[
                      {
                        data: charts.tickCrossings,
                        color: "#fbbf24",
                        label: "Crossings per round",
                        fill: true,
                      },
                    ]}
                  />
                  <p
                    style={{
                      color: "var(--text-2)",
                      fontSize: ".78rem",
                      marginTop: 8,
                    }}
                  >
                    Each spike = a swap consumed all liquidity in the
                    active tick and stepped to the next one. Frequent
                    crossings mean LP positions are getting churned
                    through; flat regions mean trades stayed inside one
                    tick (zero slippage from tick-crossing).
                  </p>
                </Card>
              )}
              <Card
                title="Fees by Destination"
                actions={embedButton("fees-by-destination", "Fees by Destination")}
              >
                {charts.feesByDestination.length === 0 ? (
                  <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No fee data.</p>
                ) : (
                  <ChartCanvas
                    height={240}
                    decimals={0}
                    stacked
                    // Data arrives unstacked and sorted largest-first;
                    // ChartCanvas handles the visual stack so tooltips
                    // can show each destination's own cumulative value.
                    datasets={charts.feesByDestination.map((series, i) => {
                      const palette = ["#34d399", "#6c8aff", "#fbbf24", "#a78bfa", "#22d3ee", "#f472b6"];
                      return {
                        data: series.data,
                        color: palette[i % palette.length],
                        label: series.destination,
                        fill: true,
                      };
                    })}
                  />
                )}
                <CalibrationBand
                  input={bandInputForFamily("total_volume")}
                  metricLabel="Total volume"
                />
              </Card>
              {replayMetrics && (
                <Card title="Replay metrics" style={{ gridColumn: "1 / -1" }}>
                  <ReplayMetricsGrid
                    metrics={replayMetrics}
                    calibrationBands={replayMetricCalibrationBands}
                    embedActionForMetric={(metricKey) =>
                      embedButton(metricKey, REPLAY_METRIC_LABELS[metricKey])
                    }
                  />
                  <CalibrationBand
                    input={bandInputForFamily("tips_paid")}
                    metricLabel="Replay metric bands"
                  />
                </Card>
              )}
            </div>
          </div>
        )}

        {/* ── Agents Tab ──────────────────────────────────── */}
        {activeTab === "agents" && (
          <div className="tab-panel active">
            <Card
              title="Agent Final States"
              actions={
                <div style={{ display: "flex", gap: 8 }}>
                  <select
                    value={agentRoleFilter}
                    onChange={(e) => setAgentRoleFilter(e.target.value)}
                    data-testid="agent-role-filter"
                  >
                    <option value="all">All Roles</option>
                    {roleFilterOptions.map((role) => (
                      <option key={role} value={role}>
                        {role}
                      </option>
                    ))}
                  </select>
                  <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                    <option value="pnl">Sort by PnL</option>
                    <option value="volume">Sort by Volume</option>
                    <option value="balance">Sort by Balance</option>
                  </select>
                </div>
              }
            >
              <div className="table-wrap" style={{ maxHeight: 400, overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Agent</th>
                      <th>Role</th>
                      <th>Balance</th>
                      <th>Volume</th>
                      <th title={`Realized PnL, denominated in ${denomLabel}`}>
                        PnL ({denomLabel})
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredAgents.length === 0 ? (
                      <tr>
                        <td colSpan={5} style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                          No agents matched the filter.
                        </td>
                      </tr>
                    ) : (
                      filteredAgents.map((a) => (
                        <tr
                          key={a.agentId}
                          style={{ cursor: "pointer" }}
                          onClick={() => setSelectedAgent(a)}
                        >
                          <td className="mono">{a.agentId}</td>
                          <td>
                            <span style={{ color: hashColorVar(a.role) }}>{a.role}</span>
                          </td>
                          <td className="mono">{a.balance.toLocaleString()}</td>
                          <td className="mono">{a.volume.toLocaleString()}</td>
                          <td
                            className="mono"
                            style={{ color: a.pnl >= 0 ? "var(--green)" : "var(--red)" }}
                            title={`Realized PnL in ${denomLabel}`}
                          >
                            {formatPnl(a.pnl, market, { result: bundleResult })}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}

        {/* ── Events Tab ──────────────────────────────────── */}
        {activeTab === "events" && (
          <div className="tab-panel active">
            <Card title={`Event Log${events.length > 0 ? ` (${events.length})` : ""}`}>
              {eventsLoading && events.length === 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <Skeleton height={20} />
                  <Skeleton height={20} />
                  <Skeleton height={20} />
                </div>
              )}
              {eventsError && (
                <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{eventsError}</p>
              )}
              {!eventsLoading && events.length === 0 && !eventsError && (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  No events recorded for this run.
                </p>
              )}
              {events.length > 0 && (
                <div className="event-log" style={{ maxHeight: 500, overflowY: "auto" }}>
                  {events.map((ev, i) => (
                    <div key={i} className={`event-row ${ev.cls}`}>
                      <span className="event-round mono">R{ev.round}</span>
                      <span className="event-type">{ev.evType}</span>
                      <span className="event-detail">{ev.detail}</span>
                    </div>
                  ))}
                </div>
              )}
              {events.length > 0 && !eventsExhausted && (
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ marginTop: 8 }}
                  onClick={loadMoreEvents}
                  disabled={eventsLoading}
                >
                  {eventsLoading ? "Loading…" : "Load more"}
                </button>
              )}
            </Card>
          </div>
        )}

        {/* ── Solana Tab (lighthouse run-page surfaces) ───── */}
        {activeTab === "solana" && isSolanaRun && (
          <div
            className="tab-panel active"
            data-testid="results-solana-tab"
            data-solana-run="true"
          >
            <p
              style={{
                color: "var(--text-2)",
                fontSize: ".82rem",
                marginBottom: 12,
              }}
            >
              Solana-specific views: bundle outcomes, the priority-fee
              market, validator revenue, and the Jito searcher&apos;s
              landing rate and tip ROI. Counts come from the per-slot
              snapshot ledger.
            </p>

            <div className="grid-2">
              <Card title="Slot timeline · bundle outcomes">
                <div data-testid="solana-bundle-outcomes">
                  <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
                    <StatCard
                      label="Slots executed"
                      value={String(numRoundsExecuted)}
                      hint="Rounds emitted"
                    />
                    <StatCard
                      label="Bundles landed"
                      value={String(bundleOutcomeCounts.landed)}
                      hint="paid tip on land"
                    />
                    <StatCard
                      label="Bundles reverted"
                      value={String(bundleOutcomeCounts.reverted)}
                      hint="partial-failure revert"
                    />
                    <StatCard
                      label="Bundles dropped"
                      value={String(bundleOutcomeCounts.dropped)}
                      hint="auction conflicts"
                    />
                    <StatCard
                      label="Avg landing rate"
                      value={
                        bundleLandingRateStats.roundsWithBundles > 0
                          ? `${(bundleLandingRateStats.avg * 100).toFixed(1)}%`
                          : "—"
                      }
                      hint={
                        bundleLandingRateStats.roundsWithBundles > 0
                          ? `± ${(bundleLandingRateStats.stdev * 100).toFixed(1)}% across ${bundleLandingRateStats.roundsWithBundles} active slot${bundleLandingRateStats.roundsWithBundles === 1 ? "" : "s"}`
                          : "no bundles submitted"
                      }
                    />
                  </div>
                  {bundleOutcomeCounts.landed +
                    bundleOutcomeCounts.reverted +
                    bundleOutcomeCounts.dropped >
                    0 && (
                    <div
                      data-testid="solana-bundle-outcomes-chart"
                      style={{ marginTop: 12 }}
                    >
                      <ChartCanvas
                        height={200}
                        decimals={0}
                        stacked
                        datasets={[
                          {
                            data: bundleOutcomeTimeline.landed,
                            color: "#22c55e",
                            label: "Landed",
                            fill: true,
                            alpha: 0.7,
                          },
                          {
                            data: bundleOutcomeTimeline.reverted,
                            color: "#fbbf24",
                            label: "Reverted",
                            fill: true,
                            alpha: 0.7,
                          },
                          {
                            data: bundleOutcomeTimeline.dropped,
                            color: "#ef4444",
                            label: "Dropped",
                            fill: true,
                            alpha: 0.7,
                          },
                        ]}
                      />
                      <p
                        style={{
                          color: "var(--text-2)",
                          fontSize: ".78rem",
                          marginTop: 8,
                        }}
                      >
                        Stacked per-slot bundle outcomes. Watch for bands that
                        flip colour mid-run — that&apos;s the searcher
                        starting to lose auctions or the auction starting to
                        revert bundles.
                      </p>
                    </div>
                  )}
                  {Object.keys(dropReasonCounts).length > 0 && (
                    <div
                      data-testid="solana-bundle-drop-reasons"
                      style={{ marginTop: 12 }}
                    >
                      <div
                        style={{
                          fontSize: ".78rem",
                          color: "var(--text-2)",
                          marginBottom: 4,
                          textTransform: "uppercase",
                          letterSpacing: ".05em",
                        }}
                      >
                        Drop breakdown
                      </div>
                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          gap: 8,
                        }}
                      >
                        {Object.entries(dropReasonCounts)
                          .sort((a, b) => b[1] - a[1])
                          .map(([reason, count]) => (
                            <span
                              key={reason}
                              className="mono"
                              style={{
                                fontSize: ".78rem",
                                padding: "2px 8px",
                                borderRadius: 4,
                                background: "var(--surface-2, #1a1a1f)",
                                color: "var(--text-1)",
                              }}
                            >
                              <span style={{ color: "var(--text-2)" }}>
                                {reason}
                              </span>
                              {" · "}
                              {count.toLocaleString()}
                            </span>
                          ))}
                      </div>
                    </div>
                  )}
                  <p style={{ color: "var(--text-2)", fontSize: ".78rem", marginTop: 12 }}>
                    Drops here are auction-stage conflicts: lock conflict,
                    compute-unit budget, tip below minimum, bundle too large.
                    Drops that happen before the auction (RPC / TPU / Jito
                    submission failures) are in the Events tab.
                  </p>
                </div>
              </Card>

              <Card title="Priority fee market">
                <div data-testid="solana-fee-market">
                  <StatCard
                    label="Fee-market updates"
                    value={String(
                      events.filter(
                        (e) => e.evType === "PRIORITY_FEE_MARKET_UPDATED",
                      ).length,
                    )}
                    hint="percentile shifts past threshold"
                  />
                  {priorityFeeMarketChart.series.length === 0 ? (
                    <p
                      style={{
                        color: "var(--text-2)",
                        fontSize: ".78rem",
                        marginTop: 8,
                      }}
                    >
                      No priority-fee market updates recorded for this run.
                      The chart populates once write-lock contention
                      re-prices a hot pool past the configured update
                      threshold.
                    </p>
                  ) : (
                    <div
                      data-testid="solana-fee-market-chart"
                      style={{ marginTop: 12 }}
                    >
                      <ChartCanvas
                        height={240}
                        decimals={0}
                        datasets={priorityFeeMarketChart.series.map((s) => {
                          const accountIdx =
                            priorityFeeMarketChart.accounts.indexOf(
                              s.accountId,
                            );
                          // Per-account hue, per-percentile alpha so the
                          // five lines for one pool stay readable on the
                          // same chart. p99 is opaque, p25 is faintest.
                          const palette = [
                            "#22d3ee",
                            "#6c8aff",
                            "#a78bfa",
                            "#fbbf24",
                          ];
                          const color = palette[accountIdx % palette.length];
                          const alphaByPct: Record<number, number> = {
                            25: 0.35,
                            50: 0.6,
                            75: 0.75,
                            90: 0.9,
                            99: 1.0,
                          };
                          return {
                            data: s.data,
                            color,
                            label: `${s.accountId} p${s.percentile}`,
                            fill: false,
                            alpha: alphaByPct[s.percentile] ?? 0.7,
                            width: s.percentile === 50 ? 2 : 1,
                          };
                        })}
                      />
                      <p
                        style={{
                          color: "var(--text-2)",
                          fontSize: ".78rem",
                          marginTop: 8,
                        }}
                      >
                        One line per (account, percentile) over the run.
                        Showing the {priorityFeeMarketChart.accounts.length}{" "}
                        hottest pool{priorityFeeMarketChart.accounts.length === 1
                          ? ""
                          : "s"}{" "}
                        by update count.
                      </p>
                    </div>
                  )}
                </div>
              </Card>

              <Card title="Validator revenue · tips paid">
                <div data-testid="solana-validator-revenue">
                  <StatCard
                    label="Tips paid total"
                    value={tipsPaidTotalLamports.toLocaleString()}
                    hint="lamports (validator + stake pool)"
                  />
                  <p
                    style={{
                      color: "var(--text-2)",
                      fontSize: ".78rem",
                      marginTop: 8,
                    }}
                  >
                    Total tips paid out on every landed bundle, summed
                    across the validator and stake-pool sides. Per-slot
                    breakdown by validator is not yet shown.
                  </p>
                </div>
              </Card>

              <Card title="JitoSearcher · landing rate + tip ROI">
                <div data-testid="solana-jito-metrics">
                  {jitoSearcherSummary?.calibration ? (
                    <div
                      data-testid="jito-calibration-footer"
                      data-jito-calibrated="true"
                      style={{
                        marginBottom: 8,
                        color: "var(--text-2)",
                        fontSize: ".78rem",
                      }}
                    >
                      Calibrated against{" "}
                      <span className="mono">
                        {Number(
                          jitoSearcherSummary.calibration.n_bundles ?? 0,
                        ).toLocaleString()}
                      </span>{" "}
                      bundles captured{" "}
                      <span className="mono">
                        {String(jitoSearcherSummary.calibration.captured_at ?? "")}
                      </span>
                      .
                    </div>
                  ) : jitoSearcherSummary?.synthetic ? (
                    <div style={{ marginBottom: 8 }}>
                      <span
                        className="synthetic-badge"
                        data-synthetic-marker="jito"
                        title="The bundle-auction mechanics, fee accounting, and pool state are real. The probability of any given bundle landing — and the tip needed to land it — are illustrative, not yet measured against real outcomes."
                        style={{ display: "inline-block" }}
                      >
                        uncalibrated landing rate
                      </span>
                    </div>
                  ) : null}
                  {jitoSearcherSummary ? (
                    <>
                      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                        <StatCard
                          label="Landing rate"
                          value={`${(jitoSearcherSummary.landingRate * 100).toFixed(2)}%`}
                          hint={`${jitoSearcherSummary.bundlesLanded.toLocaleString()} of ${jitoSearcherSummary.bundlesSubmitted.toLocaleString()} bundles`}
                        />
                        <StatCard
                          label="Tip ROI"
                          value={`${jitoSearcherSummary.tipRoi.toFixed(2)}×`}
                          hint={`${jitoSearcherSummary.realizedEv.toLocaleString()} EV / ${jitoSearcherSummary.tipsPaid.toLocaleString()} tip`}
                        />
                        <StatCard
                          label="Tips paid"
                          value={jitoSearcherSummary.tipsPaid.toLocaleString()}
                          hint="lamports paid on landed bundles"
                        />
                      </div>
                      <p
                        style={{
                          color: "var(--text-2)",
                          fontSize: ".78rem",
                          marginTop: 8,
                        }}
                      >
                        How often the searcher&apos;s bundles land and how
                        much they earn per lamport of tip paid. The math
                        and pool state are real; the landing-rate model
                        itself is illustrative until it&apos;s calibrated
                        against real Jito auction outcomes.
                      </p>
                    </>
                  ) : (
                    <>
                      <StatCard
                        label="Landing rate"
                        value="—"
                        hint="No JitoSearcher in spec"
                      />
                      <StatCard
                        label="Tip ROI"
                        value="—"
                        hint="No JitoSearcher in spec"
                      />
                      <p
                        style={{
                          color: "var(--text-2)",
                          fontSize: ".78rem",
                          marginTop: 8,
                        }}
                      >
                        No <code>jito_searcher</code> metrics on the final
                        snapshot — this run did not include a JitoSearcher
                        agent.
                      </p>
                    </>
                  )}
                </div>
              </Card>
            </div>

            {eventsLoading && events.length === 0 && (
              <p
                style={{
                  color: "var(--text-2)",
                  fontSize: ".78rem",
                  marginTop: 12,
                }}
              >
                Loading event log to populate counts…
              </p>
            )}
          </div>
        )}

        {/* ── Exports Tab ─────────────────────────────────── */}
        {activeTab === "exports" && (
          <div className="tab-panel active">
            <Card title="Export Results">
              <div className="grid-3">
                <div className="stat-card" style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>CSV</div>
                  <p style={{ fontSize: ".82rem", color: "var(--text-2)", marginBottom: 8 }}>
                    {idiom.round_label} snapshots with agent states and prices, flattened.
                  </p>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleExport("csv")}
                    disabled={exporting !== null}
                  >
                    {exporting === "csv" ? "Exporting…" : "Download .csv"}
                  </button>
                </div>
                <div className="stat-card" style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>JSON</div>
                  <p style={{ fontSize: ".82rem", color: "var(--text-2)", marginBottom: 8 }}>
                    Structured simulation result with metadata.
                  </p>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleExport("json")}
                    disabled={exporting !== null}
                  >
                    {exporting === "json" ? "Exporting…" : "Download .json"}
                  </button>
                </div>
                <div className="stat-card" style={{ padding: 16 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Parquet</div>
                  <p style={{ fontSize: ".82rem", color: "var(--text-2)", marginBottom: 8 }}>
                    Columnar format for large datasets.
                  </p>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleExport("parquet")}
                    disabled={exporting !== null}
                  >
                    {exporting === "parquet" ? "Exporting…" : "Download .parquet"}
                  </button>
                </div>
              </div>
            </Card>
          </div>
        )}
      </div>

      {/* Agent Story Drawer */}
      {selectedAgent && (
        <AgentStoryView
          runId={runId}
          totalRounds={numRoundsExecuted}
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </>
  );
}
