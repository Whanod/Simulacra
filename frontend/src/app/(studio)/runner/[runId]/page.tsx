"use client";

import { useState, useRef, useEffect, useCallback, use } from "react";
import { useRouter } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import { useStudioStore } from "@/lib/state/useStudioStore";
import Modal from "@/components/feedback/Modal";
import Card from "@/components/ui/Card";
import StatCard from "@/components/ui/StatCard";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { ChartCanvas } from "@/components/charts";
import SnapshotPanel from "@/features/runner/SnapshotPanel";
import DrilldownDrawer from "@/features/runner/DrilldownDrawer";
import type { EvEntry, RunSpec } from "@/lib/types";
import { useChainIdiom } from "@/lib/hooks/useChainIdiom";
import { useDataTheme } from "@/lib/hooks/useDataTheme";
import { simulationService } from "@/lib/services/simulationService";
import { runnerService } from "@/lib/services/runnerService";
import { ApiError, toToastMessage } from "@/lib/api/errors";
import { marketSeriesFromSnapshot } from "@/lib/api/adapters/runner";
import type {
  ApiMarketSnapshotRaw,
  ParameterStoreView,
  RoundDelta,
  ViolationRow,
} from "@/lib/api/adapters/runner";

const PLOT_COLORS = ["#6c8aff", "#34d399", "#fbbf24", "#a78bfa", "#22d3ee", "#f472b6"];

type RunnerPhase = "loading" | "ready" | "completed" | "lost" | "error";

export default function RunnerPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = use(params);
  const router = useRouter();
  const { showToast } = useToast();
  const {
    runner,
    setIsPlaying,
    setSpeed,
    setEvFilter,
    resetRunner,
    initializeRunner,
    setRunnerMarketData,
    applyStep,
    appendEvents,
    setStoppedReason,
    interactiveEngines,
    clearInteractiveEngine,
  } = useStudioStore();

  const eventLogRef = useRef<HTMLDivElement>(null);

  // ── Engine wiring ─────────────────────────────────────
  const simulationId = interactiveEngines[runId] ?? null;
  const [phase, setPhase] = useState<RunnerPhase>("loading");
  const [phaseError, setPhaseError] = useState<string | null>(null);

  // Refs to drive the interval without re-creating it on every state update.
  const playRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const isPlayingRef = useRef(false);
  const speedRef = useRef(runner.speed);
  const cumVolRef = useRef(0);
  const isCompleteRef = useRef(false);
  const tokensRef = useRef<string[]>([]);
  const inFlightStepRef = useRef(false);
  const selectedMarketRef = useRef<string | null>(null);
  const hydratedMarketViewRef = useRef<string | null>(null);

  // ── Side panels ───────────────────────────────────────
  const [marketStates, setMarketStates] = useState<
    Array<{ name: string; snapshot: ApiMarketSnapshotRaw }>
  >([]);
  const [selectedMarketName, setSelectedMarketName] = useState<string | null>(null);
  const [paramStore, setParamStore] = useState<ParameterStoreView | null>(null);
  const [violations, setViolations] = useState<ViolationRow[]>([]);

  // Param modal state
  const [paramModalOpen, setParamModalOpen] = useState(false);
  const [violationsModalOpen, setViolationsModalOpen] = useState(false);
  const [paramKey, setParamKey] = useState("fee_rate");
  const [paramValue, setParamValue] = useState("0.005");
  const [paramMode, setParamMode] = useState("immediate");
  const [paramRound, setParamRound] = useState(100);
  const [paramSubmitting, setParamSubmitting] = useState(false);

  // Drilldown state
  const [drilldownOpen, setDrilldownOpen] = useState(false);
  const [drilldownEvent, setDrilldownEvent] = useState<EvEntry | undefined>();
  const [drilldownRound, setDrilldownRound] = useState<number | undefined>();

  // Chain idiom for time-unit vocabulary
  const [runSpec, setRunSpec] = useState<RunSpec | null>(null);
  const idiom = useChainIdiom(runSpec);
  useDataTheme(runSpec);

  // ── Mount / lifecycle ─────────────────────────────────
  useEffect(() => {
    isPlayingRef.current = runner.isPlaying;
  }, [runner.isPlaying]);
  useEffect(() => {
    speedRef.current = runner.speed;
  }, [runner.speed]);
  useEffect(() => {
    cumVolRef.current = runner.cumulativeVolume;
  }, [runner.cumulativeVolume]);
  useEffect(() => {
    tokensRef.current = runner.tokens;
  }, [runner.tokens]);
  useEffect(() => {
    selectedMarketRef.current = selectedMarketName;
  }, [selectedMarketName]);

  // Cleanup on unmount: stop the interval, but DO NOT delete the engine —
  // the user may navigate back. Phase 4 can revisit this.
  useEffect(
    () => () => {
      if (playRef.current) clearInterval(playRef.current);
      playRef.current = null;
    },
    [],
  );

  // Reset runner state when navigating to a new runId.
  useEffect(() => {
    resetRunner();
    setPhaseError(null);
    setPhase("loading");
    setMarketStates([]);
    setSelectedMarketName(null);
    setParamStore(null);
    setViolations([]);
    isCompleteRef.current = false;
    cumVolRef.current = 0;
    tokensRef.current = [];
    selectedMarketRef.current = null;
    hydratedMarketViewRef.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // Initial load — figure out whether this is a live engine, a completed run,
  // or a lost engine, then bootstrap the corresponding view.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (simulationId) {
        try {
          const status = await runnerService.getStatus(simulationId);
          if (cancelled) return;
          isCompleteRef.current = status.isComplete;

          // Pull spec to learn `num_rounds` for the progress bar.
          const run = await simulationService.getRun(runId);
          const totalRounds = run?.totalRounds ?? 0;
          if (run?.spec) setRunSpec(run.spec);
          initializeRunner({
            totalRounds,
            currentRound: status.currentRound,
            tokens: [],
          });

          // Markets / parameters / events / hook in parallel.
          const [markets, params, events] = await Promise.all([
            runnerService.getAllMarkets(simulationId).catch(() => []),
            runnerService.getParameters(simulationId).catch(() => null),
            runnerService.getEngineEvents(simulationId, { limit: 200 }).catch(() => []),
          ]);
          if (cancelled) return;

          setMarketStates(markets);
          setParamStore(params);
          if (events.length > 0) appendEvents(events);

          // Best-effort hook attach. Failures here aren't fatal.
          try {
            await runnerService.attachValidationHook(simulationId);
            const v = await runnerService.getViolations(simulationId);
            if (!cancelled) setViolations(v);
          } catch {
            /* ignore — hook may already exist */
          }

          if (status.isComplete) {
            setPhase("completed");
            setStoppedReason("Engine already complete");
            showToast("Engine already complete — read-only view", "info");
          } else {
            setPhase("ready");
          }
        } catch (err) {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 404) {
            // Backend lost the engine. Drop our stale handle and offer recovery.
            clearInteractiveEngine(runId);
            setPhase("lost");
            return;
          }
          setPhaseError(toToastMessage(err));
          setPhase("error");
        }
        return;
      }

      // No simulation handle in the store: this run came from a sync build,
      // or we lost it on refresh. Look up the durable run and decide.
      try {
        const run = await simulationService.getRun(runId);
        if (cancelled) return;
        if (!run) {
          setPhase("lost");
          return;
        }
        if (run.spec) setRunSpec(run.spec);
        if (run.status === "completed") {
          showToast("Run completed — opening results view", "info");
          router.replace(`/results/${runId}`);
          return;
        }
        // Run exists but the live engine handle was lost (e.g. backend restart).
        setPhase("lost");
      } catch (err) {
        if (cancelled) return;
        setPhaseError(toToastMessage(err));
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, simulationId]);

  useEffect(() => {
    if (marketStates.length === 0) return;
    const current =
      selectedMarketName && marketStates.some((market) => market.name === selectedMarketName)
        ? selectedMarketName
        : marketStates[0].name;
    if (current !== selectedMarketName) {
      setSelectedMarketName(current);
      return;
    }
    if (hydratedMarketViewRef.current === current) return;
    const selected = marketStates.find((market) => market.name === current);
    if (!selected) return;
    const series = marketSeriesFromSnapshot(selected.snapshot);
    setRunnerMarketData({
      tokens: series.tokens,
      prices: series.prices,
      reserves: series.reserves,
    });
    hydratedMarketViewRef.current = current;
  }, [marketStates, selectedMarketName, setRunnerMarketData]);

  // ── Step driver ───────────────────────────────────────
  const stopPlay = useCallback(() => {
    if (playRef.current) {
      clearInterval(playRef.current);
      playRef.current = null;
    }
    isPlayingRef.current = false;
    setIsPlaying(false);
  }, [setIsPlaying]);

  const performStep = useCallback(async (): Promise<RoundDelta | null> => {
    if (!simulationId || isCompleteRef.current || inFlightStepRef.current) return null;
    inFlightStepRef.current = true;
    try {
      const delta = await runnerService.step(simulationId, {
        totalCumulativeVolume: cumVolRef.current,
      }, selectedMarketRef.current);
      cumVolRef.current = delta.totalCumulativeVolume;
      if (delta.isComplete) {
        isCompleteRef.current = true;
      }
      applyStep(delta);
      return delta;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        if (err.status === 409) {
          isCompleteRef.current = true;
          setStoppedReason("Engine reported complete");
          showToast("Simulation complete", "success");
        } else {
          isCompleteRef.current = true;
          setStoppedReason("Engine no longer in memory");
          setPhase("lost");
          showToast("Engine lost (backend restart?)", "error");
        }
        stopPlay();
        return null;
      }
      showToast(`Step failed: ${toToastMessage(err)}`, "error");
      stopPlay();
      throw err;
    } finally {
      inFlightStepRef.current = false;
    }
  }, [simulationId, applyStep, setStoppedReason, showToast, stopPlay]);

  const restartPlayLoop = useCallback(
    (speed: number, runImmediately: boolean) => {
      if (playRef.current) clearInterval(playRef.current);
      const tick = async () => {
        if (!isPlayingRef.current || isCompleteRef.current) return;
        const delta = await performStep().catch(() => null);
        if (delta?.isComplete) stopPlay();
      };
      const interval = Math.max(100, 1000 / Math.max(1, speed));
      playRef.current = setInterval(() => {
        void tick();
      }, interval);
      if (runImmediately) {
        void tick();
      }
    },
    [performStep, stopPlay],
  );

  const startPlay = useCallback(() => {
    if (!simulationId || isCompleteRef.current) return;
    setIsPlaying(true);
    isPlayingRef.current = true;
    restartPlayLoop(speedRef.current, true);
  }, [simulationId, restartPlayLoop, setIsPlaying]);

  const togglePlay = useCallback(() => {
    if (isPlayingRef.current) stopPlay();
    else startPlay();
  }, [startPlay, stopPlay]);

  const handleStepOnce = useCallback(async () => {
    if (!simulationId || isCompleteRef.current) return;
    await performStep();
  }, [simulationId, performStep]);

  const handleStepN = useCallback(
    async (n: number) => {
      if (!simulationId || isCompleteRef.current) return;
      for (let i = 0; i < n && !isCompleteRef.current; i++) {
        const delta = await performStep();
        if (!delta || delta.isComplete) break;
      }
    },
    [simulationId, performStep],
  );

  const handleSpeedChange = useCallback(
    (s: number) => {
      setSpeed(s);
      speedRef.current = s;
      if (isPlayingRef.current && simulationId) {
        restartPlayLoop(s, false);
      }
    },
    [restartPlayLoop, setSpeed, simulationId],
  );

  const handleCancel = useCallback(async () => {
    if (!simulationId) return;
    stopPlay();
    try {
      await runnerService.cancel(simulationId);
      isCompleteRef.current = true;
      setStoppedReason("Cancelled");
      showToast(`Cancelled at round ${runner.liveRound}`, "info");
    } catch (err) {
      showToast(`Cancel failed: ${toToastMessage(err)}`, "error");
    }
  }, [simulationId, stopPlay, runner.liveRound, setStoppedReason, showToast]);

  // Refresh markets / params / violations every ~5 rounds.
  useEffect(() => {
    if (!simulationId || phase !== "ready") return;
    if (runner.liveRound === 0) return;
    if (runner.liveRound % 5 !== 0) return;
    let cancelled = false;
    (async () => {
      try {
        const [markets, params, v] = await Promise.all([
          runnerService.getAllMarkets(simulationId).catch(() => null),
          runnerService.getParameters(simulationId).catch(() => null),
          runnerService.getViolations(simulationId).catch(() => null),
        ]);
        if (cancelled) return;
        if (markets) setMarketStates(markets);
        if (params) setParamStore(params);
        if (v) setViolations(v);
      } catch {
        /* swallow — these are best-effort polls */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [simulationId, phase, runner.liveRound]);

  // ── Drilldown wiring ──────────────────────────────────
  const handleEventClick = useCallback((ev: EvEntry) => {
    setDrilldownEvent(ev);
    setDrilldownRound(ev.round);
    setDrilldownOpen(true);
  }, []);

  const handleChartPointClick = useCallback((index: number) => {
    setDrilldownEvent(undefined);
    setDrilldownRound(index);
    setDrilldownOpen(true);
  }, []);

  // Auto-scroll event log on round change
  useEffect(() => {
    if (eventLogRef.current) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight;
    }
  }, [runner.events.length]);

  // ── Param edit handler ────────────────────────────────
  const handleParamApply = useCallback(async () => {
    if (!simulationId || paramSubmitting) return;
    setParamSubmitting(true);
    try {
      const value: unknown = (() => {
        const trimmed = paramValue.trim();
        if (trimmed === "true") return true;
        if (trimmed === "false") return false;
        const n = Number(trimmed);
        if (!Number.isNaN(n) && trimmed !== "") return n;
        return paramValue;
      })();
      if (paramMode === "scheduled") {
        await runnerService.scheduleParameter(simulationId, paramKey, value, paramRound);
        showToast(`Scheduled ${paramKey} → ${paramValue} at round ${paramRound}`, "success");
      } else {
        await runnerService.setParameter(simulationId, paramKey, value);
        showToast(`Set ${paramKey} = ${paramValue}`, "success");
      }
      const params = await runnerService.getParameters(simulationId);
      setParamStore(params);
      setParamModalOpen(false);
    } catch (err) {
      showToast(`Parameter update failed: ${toToastMessage(err)}`, "error");
    } finally {
      setParamSubmitting(false);
    }
  }, [
    simulationId,
    paramMode,
    paramKey,
    paramValue,
    paramRound,
    paramSubmitting,
    showToast,
  ]);

  // ── Derived view data ─────────────────────────────────
  const filteredEvents = runner.events.filter(
    (ev) => runner.evFilter === "all" || ev.cls === runner.evFilter,
  );
  const totalRounds = runner.totalRounds || 200;
  const tokens = runner.tokens.length > 0 ? runner.tokens : ["TKN-0"];
  const selectedMarket =
    (selectedMarketName
      ? marketStates.find((market) => market.name === selectedMarketName)
      : undefined) ?? marketStates[0];

  const liquiditySummary = (() => {
    if (!selectedMarket) return null;
    return marketSeriesFromSnapshot(selectedMarket.snapshot).totalLiquidity;
  })();

  // ── Render: error / lost / loading states ─────────────
  if (phase === "loading") {
    return (
      <>
        <Topbar title="Live Runner" spec={runSpec} />
        <div id="content" className="fade-in">
          <Card title="Loading runner">
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

  if (phase === "error") {
    return (
      <>
        <Topbar title="Live Runner" spec={runSpec} />
        <div id="content" className="fade-in">
          <Card title="Runner failed to load">
            <p style={{ color: "var(--red)", fontSize: ".88rem" }}>
              {phaseError ?? "Unknown error."}
            </p>
            <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
              <button
                className="btn btn-secondary"
                onClick={() => router.push("/dashboard")}
              >
                Back to Dashboard
              </button>
            </div>
          </Card>
        </div>
      </>
    );
  }

  if (phase === "lost") {
    return (
      <>
        <Topbar title="Live Runner" spec={runSpec} />
        <div id="content" className="fade-in">
          <Card title="Live engine no longer in memory">
            <p style={{ color: "var(--text-2)", fontSize: ".88rem", marginBottom: 12 }}>
              The backend doesn&rsquo;t have a live engine for this run. This usually
              happens after a backend restart or after the engine completed and was
              cleaned up. You can still view results or fork from a snapshot.
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                className="btn btn-primary"
                onClick={() => router.push(`/results/${runId}`)}
              >
                View Results
              </button>
              <button
                className="btn btn-secondary"
                onClick={() => router.push("/dashboard")}
              >
                Back to Dashboard
              </button>
            </div>
          </Card>
          <SnapshotPanel runId={runId} currentRound={0} simulationId={null} />
        </div>
      </>
    );
  }

  const isReadOnly = phase === "completed";

  return (
    <>
      <Topbar title="Live Runner" spec={runSpec} />
      <div id="content" className="fade-in">
        {/* ── Live Controls ─────────────────────────────── */}
        <div className="live-controls">
          <button
            className="btn btn-success"
            onClick={togglePlay}
            disabled={isReadOnly || isCompleteRef.current}
          >
            {runner.isPlaying ? (
              <>
                <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
                  <rect x="4" y="3" width="3" height="12" rx="1" fill="currentColor" />
                  <rect x="11" y="3" width="3" height="12" rx="1" fill="currentColor" />
                </svg>{" "}
                Pause
              </>
            ) : (
              <>
                <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
                  <polygon points="4,2 16,9 4,16" fill="currentColor" />
                </svg>{" "}
                Play
              </>
            )}
          </button>
          <button
            className="btn btn-secondary"
            onClick={handleStepOnce}
            disabled={isReadOnly || isCompleteRef.current}
          >
            Step +1
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => handleStepN(10)}
            disabled={isReadOnly || isCompleteRef.current}
          >
            Step +10
          </button>
          <button
            className="btn btn-danger btn-sm"
            onClick={handleCancel}
            disabled={isReadOnly || isCompleteRef.current}
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="violations-badge"
            onClick={() => setViolationsModalOpen(true)}
            title={
              violations.length === 0
                ? "No invariant violations"
                : `${violations.length} invariant violation${violations.length === 1 ? "" : "s"} — click to view`
            }
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 10px",
              borderRadius: 999,
              border: "1px solid var(--border)",
              background:
                violations.length === 0
                  ? "var(--surface-2)"
                  : "var(--red-dim, rgba(248, 113, 113, 0.15))",
              color:
                violations.length === 0 ? "var(--text-2)" : "var(--red)",
              fontSize: ".78rem",
              cursor: "pointer",
            }}
          >
            <span>⚠</span>
            <span>
              {violations.length} violation{violations.length === 1 ? "" : "s"}
            </span>
          </button>
          <div className="speed-control">
            <span>Speed</span>
            <input
              type="range"
              min={1}
              max={10}
              value={runner.speed}
              onChange={(e) => handleSpeedChange(parseInt(e.target.value))}
            />
            <span>{runner.speed}&times;</span>
          </div>
          <div className="round-display">
            {idiom.round_label} <span>{runner.liveRound}</span> / <span>{totalRounds}</span>
          </div>
          <div className="progress-bar" style={{ maxWidth: 200 }}>
            <div
              className="fill blue"
              style={{
                width: `${Math.min(100, (runner.liveRound / Math.max(1, totalRounds)) * 100)}%`,
              }}
            />
          </div>
        </div>

        {/* ── Split layout ─────────────────────────────── */}
        <div className="split-h">
          {/* Left side: charts */}
          <div>
            <Card title="Live Price">
              <ChartCanvas
                height={200}
                onPointClick={handleChartPointClick}
                datasets={runner.livePrices.map((data, i) => ({
                  data,
                  color: PLOT_COLORS[i % PLOT_COLORS.length],
                  label: tokens[i] ?? `TKN-${i}`,
                }))}
              />
            </Card>
            <Card title="Reserves">
              <ChartCanvas
                height={180}
                decimals={0}
                datasets={runner.liveReserves.map((data, i) => ({
                  data,
                  color: PLOT_COLORS[i % PLOT_COLORS.length],
                  label: tokens[i] ?? `TKN-${i}`,
                }))}
              />
            </Card>
            <Card title="Agent Balances">
              <ChartCanvas
                height={180}
                decimals={0}
                datasets={Object.entries(runner.liveBalances)
                  .slice(0, 6)
                  .map(([id, data], i) => ({
                    data,
                    color: PLOT_COLORS[i % PLOT_COLORS.length],
                    label: id,
                  }))}
              />
            </Card>
          </div>

          {/* Right side: state, params, events, checks */}
          <div>
            <Card
              title="Market State"
              actions={
                marketStates.length > 1 ? (
                  <select
                    value={selectedMarketName ?? marketStates[0]?.name ?? ""}
                    onChange={(e) => {
                      hydratedMarketViewRef.current = null;
                      setSelectedMarketName(e.target.value);
                    }}
                    style={{ fontSize: ".82rem" }}
                  >
                    {marketStates.map((market) => (
                      <option key={market.name} value={market.name}>
                        {market.name}
                      </option>
                    ))}
                  </select>
                ) : null
              }
              badge={
                <Badge variant="green">
                  {selectedMarket?.snapshot.__type__ ?? "Live"}
                </Badge>
              }
            >
              <div className="grid-3" style={{ marginBottom: 12 }}>
                <StatCard
                  label="Total Liquidity"
                  value={liquiditySummary !== null ? liquiditySummary.toLocaleString() : "—"}
                  valueSize="1.1rem"
                />
                <StatCard
                  label="Volume (round)"
                  value={runner.lastVol.toLocaleString()}
                  valueSize="1.1rem"
                />
                <StatCard
                  label="Cum. Volume"
                  value={runner.cumulativeVolume.toLocaleString()}
                  valueSize="1.1rem"
                />
              </div>
              <div className="table-wrap" style={{ maxHeight: 140, overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Token</th>
                      <th>Price</th>
                      <th>Reserve</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tokens.map((tok, i) => {
                      const p = runner.tokenPrices[i] ?? 0;
                      const reserves =
                        runner.liveReserves[i]?.[runner.liveReserves[i]?.length - 1] ?? 0;
                      return (
                        <tr key={tok}>
                          <td>{tok}</td>
                          <td
                            className="mono"
                            style={{
                              color:
                                p > 1
                                  ? "var(--green)"
                                  : p < 1 && p > 0
                                    ? "var(--red)"
                                    : undefined,
                            }}
                          >
                            {p.toFixed(4)}
                          </td>
                          <td className="mono">{reserves.toLocaleString()}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card
              title="Runtime Parameters"
              actions={
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => setParamModalOpen(true)}
                  disabled={isReadOnly}
                >
                  Edit
                </button>
              }
            >
              <div className="table-wrap" style={{ maxHeight: 160, overflowY: "auto" }}>
                <table>
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Value</th>
                      <th>Pending</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(paramStore?.rows ?? []).length === 0 ? (
                      <tr>
                        <td colSpan={3} style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                          No parameters reported.
                        </td>
                      </tr>
                    ) : (
                      paramStore!.rows.map((row) => (
                        <tr key={row.key}>
                          <td className="mono">{row.key}</td>
                          <td className="mono">
                            {row.value === undefined ? "—" : String(row.value)}
                          </td>
                          <td
                            className="mono"
                            style={{ color: "var(--text-2)" }}
                          >
                            {row.pendingAtRound !== undefined
                              ? `@r${row.pendingAtRound}`
                              : "—"}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card
              title="Event Log"
              actions={
                <div style={{ display: "flex", gap: 4 }}>
                  {(["all", "trade", "lp", "fail"] as const).map((f) => (
                    <button
                      key={f}
                      className={`btn btn-secondary btn-sm ev-filter${runner.evFilter === f ? " active" : ""}`}
                      onClick={() => setEvFilter(f)}
                    >
                      {f === "all"
                        ? "All"
                        : f === "trade"
                          ? "Trades"
                          : f === "lp"
                            ? "LP"
                            : "Errors"}
                    </button>
                  ))}
                </div>
              }
            >
              <div className="event-log" ref={eventLogRef}>
                {filteredEvents.length === 0 ? (
                  <div className="ev" style={{ color: "var(--text-2)" }}>
                    <span className="ev-detail">No events yet.</span>
                  </div>
                ) : (
                  filteredEvents.map((ev, i) => (
                    <div
                      className="ev"
                      key={`${ev.round}-${i}`}
                      style={{ cursor: "pointer" }}
                      onClick={() => handleEventClick(ev)}
                    >
                      <span className="ev-round">R{ev.round}</span>
                      <span className={`ev-type ${ev.cls}`}>{ev.evType}</span>
                      <span className="ev-detail">{ev.detail}</span>
                    </div>
                  ))
                )}
              </div>
            </Card>

            <Card title="Invariant Checks">
              {violations.length === 0 ? (
                <div>
                  <div className="check-item">
                    <span className="check-icon pass">&#10003;</span> No violations reported
                  </div>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {violations.slice(-5).map((v, i) => (
                    <div key={i} className="check-item">
                      <span className="check-icon fail">&#10005;</span>
                      <span className="mono" style={{ marginRight: 6 }}>R{v.round}</span>
                      <span style={{ fontSize: ".82rem" }}>{v.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <SnapshotPanel
              runId={runId}
              currentRound={runner.liveRound}
              simulationId={simulationId}
            />
          </div>
        </div>
      </div>

      {/* Drilldown Drawer */}
      <DrilldownDrawer
        open={drilldownOpen}
        onClose={() => setDrilldownOpen(false)}
        runId={runId}
        event={drilldownEvent}
        round={drilldownRound}
      />

      {/* ── Parameter Edit Modal ───────────────────────── */}
      <Modal
        open={paramModalOpen}
        onClose={() => setParamModalOpen(false)}
        title="Edit Parameter"
        actions={
          <>
            <button
              className="btn btn-secondary"
              onClick={() => setParamModalOpen(false)}
              disabled={paramSubmitting}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={handleParamApply}
              disabled={paramSubmitting}
            >
              {paramSubmitting ? "Applying…" : "Apply"}
            </button>
          </>
        }
      >
        <div className="form-group">
          <label>Key</label>
          <input
            type="text"
            value={paramKey}
            onChange={(e) => setParamKey(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>New Value</label>
          <input
            type="text"
            value={paramValue}
            onChange={(e) => setParamValue(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Mode</label>
          <select
            value={paramMode}
            onChange={(e) => setParamMode(e.target.value)}
          >
            <option value="immediate">Immediate</option>
            <option value="scheduled">Scheduled (at {idiom.round_label.toLowerCase()})</option>
          </select>
        </div>
        {paramMode === "scheduled" && (
          <div className="form-group">
            <label>At {idiom.round_label}</label>
            <input
              type="number"
              value={paramRound}
              onChange={(e) => setParamRound(parseInt(e.target.value))}
            />
          </div>
        )}
      </Modal>

      {/* ── Violations Modal ────────────────────────────── */}
      <Modal
        open={violationsModalOpen}
        onClose={() => setViolationsModalOpen(false)}
        title={`Invariant Violations (${violations.length})`}
        maxWidth={560}
        actions={
          <button
            className="btn btn-primary"
            onClick={() => setViolationsModalOpen(false)}
          >
            Close
          </button>
        }
      >
        {violations.length === 0 ? (
          <p
            style={{ color: "var(--text-2)", fontSize: ".88rem" }}
            data-testid="violations-empty"
          >
            No invariant violations reported yet.
          </p>
        ) : (
          <div
            data-testid="violations-list"
            style={{
              maxHeight: 360,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 6,
            }}
          >
            {violations.map((v, i) => (
              <div
                key={`${v.round}-${i}`}
                className="check-item"
                style={{ alignItems: "flex-start" }}
              >
                <span className="check-icon fail">&#10005;</span>
                <span
                  className="mono"
                  style={{ marginRight: 8, color: "var(--text-2)" }}
                >
                  R{v.round}
                </span>
                <span style={{ fontSize: ".85rem" }}>{v.message}</span>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </>
  );
}
