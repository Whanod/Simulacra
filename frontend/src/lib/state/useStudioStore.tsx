"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import type { SimRun, AgentRow, EvEntry, EventFilter, Report } from "@/lib/types";
import { simulationService } from "@/lib/services/simulationService";
import type { RoundDelta } from "@/lib/api/adapters/runner";
import type { SimulationDraft } from "@/lib/types/drafts";

// ── Runner state ─────────────────────────────────────────
interface RunnerState {
  /** Current round, as reported by the most recent step. */
  liveRound: number;
  /** Total rounds the spec was configured for; 0 if unknown. */
  totalRounds: number;
  isPlaying: boolean;
  speed: number;
  events: EvEntry[];
  evFilter: EventFilter;
  /** Last-seen per-token prices (one entry per token, ordered by `tokens`). */
  tokenPrices: number[];
  tokens: string[];
  totalFees: number;
  lastVol: number;
  /** Per-token price history, one row per token, columns are rounds. */
  livePrices: number[][];
  liveReserves: number[][];
  liveBalances: Record<string, number[]>;
  /** Cumulative-volume bookkeeping so we can derive per-round volume deltas. */
  cumulativeVolume: number;
  /** Reason the engine stopped, if any. */
  stoppedReason: string | null;
}

const INITIAL_RUNNER: RunnerState = {
  liveRound: 0,
  totalRounds: 0,
  isPlaying: false,
  speed: 5,
  events: [],
  evFilter: "all",
  tokenPrices: [],
  tokens: [],
  totalFees: 0,
  lastVol: 0,
  livePrices: [],
  liveReserves: [],
  liveBalances: {},
  cumulativeVolume: 0,
  stoppedReason: null,
};

// ── Draft types ──────────────────────────────────────────
interface DraftSnapshot {
  name: string;
  runId: string;
  round: number;
}

interface ReportDraft {
  id: string;
  title: string;
  runIds: string[];
  sweepIds: string[];
}

// ── Store interface ──────────────────────────────────────
interface StudioStore {
  // Runs
  runs: SimRun[];
  selectedRunId: string | null;
  selectRun: (id: string) => void;
  agents: AgentRow[];

  // Runner
  runner: RunnerState;
  setIsPlaying: (playing: boolean) => void;
  setSpeed: (s: number) => void;
  setEvFilter: (f: EventFilter) => void;
  resetRunner: () => void;
  initializeRunner: (init: {
    totalRounds?: number;
    currentRound?: number;
    tokens?: string[];
  }) => void;
  setRunnerMarketData: (data: {
    tokens: string[];
    prices: number[];
    reserves: number[];
  }) => void;
  applyStep: (delta: RoundDelta) => void;
  appendEvents: (events: EvEntry[]) => void;
  setStoppedReason: (reason: string | null) => void;

  // Interactive engine bridge (Builder → Runner)
  interactiveEngines: Record<string, string>;
  setInteractiveEngine: (runId: string, simulationId: string) => void;
  clearInteractiveEngine: (runId: string) => void;

  // Compare
  compareTargets: string[];
  toggleCompareTarget: (runId: string) => void;

  // Draft state
  draftSnapshots: DraftSnapshot[];
  addDraftSnapshot: (s: DraftSnapshot) => void;
  removeDraftSnapshot: (index: number) => void;
  reportDrafts: ReportDraft[];
  addReportDraft: (r: ReportDraft) => void;
  removeReportDraft: (id: string) => void;

  /**
   * Schema-driven builder draft (US-005). Holds a `SimulationDraft`
   * whose entities round-trip through the generic adapter without
   * coercing unknown backend fields. Null until the builder loads a
   * spec or starts a fresh simulation. The existing builder page
   * (`src/app/(studio)/builder/page.tsx`) still uses local React
   * state; US-012..US-014 port it over to this slot. Holding the
   * draft in the store now lets new pages read it without blocking
   * on that refactor.
   */
  builderDraft: SimulationDraft | null;
  setBuilderDraft: (draft: SimulationDraft | null) => void;
  updateDraftEntityParams: (
    configPath: string,
    params: Record<string, unknown>,
  ) => void;

  // Live chrome — wired to the SolanaSlotClock via RoundSnapshot.current_slot
  // / current_leader. `liveSlot === 0` is treated as the placeholder (no
  // snapshot observed yet); positive values mean "live data".
  liveSlot: number;
  liveLeader: string | null;
  setLiveSlot: (slot: number) => void;
  setLiveLeader: (leader: string | null) => void;

  // UI
  agentRoleFilter: string;
  setAgentRoleFilter: (f: string) => void;
}

interface PersistedStudioUi {
  selectedRunId: string | null;
  agentRoleFilter: string;
  compareTargets: string[];
}

const DEFAULT_PERSISTED_UI: PersistedStudioUi = {
  selectedRunId: null,
  agentRoleFilter: "all",
  compareTargets: [],
};

function loadPersistedStudioUi(): PersistedStudioUi {
  if (typeof window === "undefined") return DEFAULT_PERSISTED_UI;
  try {
    const saved = window.localStorage.getItem("studio-ui");
    if (!saved) return DEFAULT_PERSISTED_UI;
    const data = JSON.parse(saved) as Partial<PersistedStudioUi>;
    return {
      selectedRunId:
        typeof data.selectedRunId === "string" ? data.selectedRunId : null,
      agentRoleFilter:
        typeof data.agentRoleFilter === "string" && data.agentRoleFilter.length > 0
          ? data.agentRoleFilter
          : "all",
      compareTargets: Array.isArray(data.compareTargets)
        ? data.compareTargets.filter((id): id is string => typeof id === "string")
        : [],
    };
  } catch {
    return DEFAULT_PERSISTED_UI;
  }
}

const StudioContext = createContext<StudioStore | undefined>(undefined);

export function useStudioStore() {
  const store = useContext(StudioContext);
  if (!store) {
    throw new Error("useStudioStore must be used within StudioStoreProvider");
  }
  return store;
}

export default function StudioStoreProvider({ children }: { children: React.ReactNode }) {
  const [persistedUi] = useState(loadPersistedStudioUi);
  const pathname = usePathname();
  const [sharedMode, setSharedMode] = useState<boolean | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setSharedMode(pathname.startsWith("/results/") && params.get("shared") === "1");
  }, [pathname]);

  // ── Runs (loaded via services) ───────────────────────
  const [runs, setRuns] = useState<SimRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(persistedUi.selectedRunId);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [agentRoleFilter, setAgentRoleFilter] = useState(persistedUi.agentRoleFilter);

  // Load runs and agents through service layer on mount
  const initialized = useRef(false);
  useEffect(() => {
    if (sharedMode !== false) return;
    if (initialized.current) return;
    initialized.current = true;
    void simulationService
      .listRuns()
      .then(setRuns)
      .catch((err: unknown) => {
        console.error("Failed to load runs", err);
      });
    void simulationService
      .getAgents("all")
      .then(setAgents)
      .catch((err: unknown) => {
        console.error("Failed to load agents", err);
      });
  }, [sharedMode]);

  // ── Compare ───────────────────────────────────────────
  const [compareTargets, setCompareTargets] = useState<string[]>(persistedUi.compareTargets);
  const toggleCompareTarget = useCallback((runId: string) => {
    setCompareTargets((prev) => {
      if (prev.includes(runId)) return prev.filter((id) => id !== runId);
      if (prev.length >= 2) return [prev[1], runId];
      return [...prev, runId];
    });
  }, []);

  // ── Draft Snapshots ───────────────────────────────────
  const [draftSnapshots, setDraftSnapshots] = useState<DraftSnapshot[]>([]);
  const addDraftSnapshot = useCallback((s: DraftSnapshot) => {
    setDraftSnapshots((prev) => [...prev, s]);
  }, []);
  const removeDraftSnapshot = useCallback((index: number) => {
    setDraftSnapshots((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Report Drafts ─────────────────────────────────────
  const [reportDrafts, setReportDrafts] = useState<ReportDraft[]>([]);
  const addReportDraft = useCallback((r: ReportDraft) => {
    setReportDrafts((prev) => [...prev, r]);
  }, []);
  const removeReportDraft = useCallback((id: string) => {
    setReportDrafts((prev) => prev.filter((r) => r.id !== id));
  }, []);

  // ── Live chrome (Phase 1.1 producer; placeholder today) ─
  const [liveSlot, setLiveSlot] = useState<number>(0);
  const [liveLeader, setLiveLeader] = useState<string | null>(null);

  // ── Builder draft (US-005) ───────────────────────────
  const [builderDraft, setBuilderDraftState] = useState<SimulationDraft | null>(null);
  const setBuilderDraft = useCallback((draft: SimulationDraft | null) => {
    setBuilderDraftState(draft);
  }, []);
  const updateDraftEntityParams = useCallback(
    (configPath: string, params: Record<string, unknown>) => {
      setBuilderDraftState((prev) => {
        if (!prev) return prev;
        const entities = prev.entities.map((e) =>
          e.configPath === configPath
            ? { ...e, params: { ...e.params, ...params } }
            : e,
        );
        return { ...prev, entities };
      });
    },
    [],
  );

  // ── Interactive engines (Builder → Runner bridge) ────
  const [interactiveEngines, setInteractiveEngines] = useState<
    Record<string, string>
  >({});
  const setInteractiveEngine = useCallback(
    (runId: string, simulationId: string) => {
      setInteractiveEngines((prev) => ({ ...prev, [runId]: simulationId }));
    },
    [],
  );
  const clearInteractiveEngine = useCallback((runId: string) => {
    setInteractiveEngines((prev) => {
      if (!(runId in prev)) return prev;
      const next = { ...prev };
      delete next[runId];
      return next;
    });
  }, []);

  // ── Runner ────────────────────────────────────────────
  const [runner, setRunner] = useState<RunnerState>(INITIAL_RUNNER);

  const setIsPlaying = useCallback((playing: boolean) => {
    setRunner((prev) => ({ ...prev, isPlaying: playing }));
  }, []);

  const setSpeed = useCallback((s: number) => {
    setRunner((prev) => ({ ...prev, speed: s }));
  }, []);

  const setEvFilter = useCallback((f: EventFilter) => {
    setRunner((prev) => ({ ...prev, evFilter: f }));
  }, []);

  const resetRunner = useCallback(() => {
    setRunner((prev) => ({
      ...INITIAL_RUNNER,
      speed: prev.speed,
      evFilter: prev.evFilter,
    }));
    setLiveSlot(0);
    setLiveLeader(null);
  }, []);

  const initializeRunner = useCallback(
    (init: { totalRounds?: number; currentRound?: number; tokens?: string[] }) => {
      setRunner((prev) => ({
        ...prev,
        totalRounds: init.totalRounds ?? prev.totalRounds,
        liveRound: init.currentRound ?? prev.liveRound,
        tokens: init.tokens ?? prev.tokens,
        livePrices:
          init.tokens && init.tokens.length > 0
            ? init.tokens.map(() => [])
            : prev.livePrices,
        liveReserves:
          init.tokens && init.tokens.length > 0
            ? init.tokens.map(() => [])
            : prev.liveReserves,
        tokenPrices:
          init.tokens && init.tokens.length > 0
            ? init.tokens.map(() => 0)
            : prev.tokenPrices,
      }));
    },
    [],
  );

  const setRunnerMarketData = useCallback(
    (data: { tokens: string[]; prices: number[]; reserves: number[] }) => {
      setRunner((prev) => ({
        ...prev,
        tokens: data.tokens,
        tokenPrices: data.prices,
        livePrices: data.tokens.map((_t, i) => [data.prices[i] ?? 0]),
        liveReserves: data.tokens.map((_t, i) => [data.reserves[i] ?? 0]),
      }));
    },
    [],
  );

  const applyStep = useCallback((delta: RoundDelta) => {
    if (typeof delta.currentSlot === "number") {
      setLiveSlot(delta.currentSlot);
    }
    setLiveLeader(delta.currentLeader);
    setRunner((prev) => {
      // Lazy-init rows on first step if we haven't seen tokens yet.
      const tokens = prev.tokens.length > 0 ? prev.tokens : delta.tokens;
      const tokenCount = tokens.length;

      const livePrices = (() => {
        if (prev.livePrices.length === tokenCount) {
          return prev.livePrices.map((row, i) => [...row, delta.prices[i] ?? 0]);
        }
        return tokens.map((_t, i) => [delta.prices[i] ?? 0]);
      })();

      const liveReserves = (() => {
        if (prev.liveReserves.length === tokenCount) {
          return prev.liveReserves.map((row, i) => [...row, delta.reserves[i] ?? 0]);
        }
        return tokens.map((_t, i) => [delta.reserves[i] ?? 0]);
      })();

      const liveBalances: Record<string, number[]> = { ...prev.liveBalances };
      for (const [agentId, total] of Object.entries(delta.agentBalances)) {
        const existing = liveBalances[agentId] || [];
        liveBalances[agentId] = [...existing, total];
      }

      return {
        ...prev,
        liveRound: delta.round,
        tokens,
        tokenPrices: delta.prices,
        livePrices,
        liveReserves,
        liveBalances,
        cumulativeVolume: delta.totalCumulativeVolume,
        lastVol: delta.volumeDelta,
        events: delta.events.length > 0 ? [...prev.events, ...delta.events] : prev.events,
      };
    });
  }, []);

  const appendEvents = useCallback((events: EvEntry[]) => {
    if (events.length === 0) return;
    setRunner((prev) => ({ ...prev, events: [...prev.events, ...events] }));
  }, []);

  const setStoppedReason = useCallback((reason: string | null) => {
    setRunner((prev) => ({ ...prev, stoppedReason: reason }));
  }, []);

  // ── Persist UI state to localStorage ──────────────────
  useEffect(() => {
    try {
      localStorage.setItem(
        "studio-ui",
        JSON.stringify({ selectedRunId, agentRoleFilter, compareTargets }),
      );
    } catch {}
  }, [selectedRunId, agentRoleFilter, compareTargets]);

  const store = useMemo<StudioStore>(
    () => ({
      runs,
      selectedRunId,
      selectRun: setSelectedRunId,
      agents,
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
      setInteractiveEngine,
      clearInteractiveEngine,
      compareTargets,
      toggleCompareTarget,
      draftSnapshots,
      addDraftSnapshot,
      removeDraftSnapshot,
      reportDrafts,
      addReportDraft,
      removeReportDraft,
      builderDraft,
      setBuilderDraft,
      updateDraftEntityParams,
      liveSlot,
      liveLeader,
      setLiveSlot,
      setLiveLeader,
      agentRoleFilter,
      setAgentRoleFilter,
    }),
    [
      runs,
      selectedRunId,
      agents,
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
      setInteractiveEngine,
      clearInteractiveEngine,
      compareTargets,
      toggleCompareTarget,
      draftSnapshots,
      addDraftSnapshot,
      removeDraftSnapshot,
      reportDrafts,
      addReportDraft,
      removeReportDraft,
      builderDraft,
      setBuilderDraft,
      updateDraftEntityParams,
      liveSlot,
      liveLeader,
      agentRoleFilter,
      setAgentRoleFilter,
    ],
  );

  return <StudioContext.Provider value={store}>{children}</StudioContext.Provider>;
}
