import type { EvEntry } from "@/lib/types/simulations";
import { fromApiEvent, type ApiEventRaw } from "@/lib/api/adapters/runs";

// ── Backend shapes ─────────────────────────────────────────────────────────

export interface ApiAgentStateRaw {
  agent_id?: string | number;
  role?: { name?: string; tags?: string[] };
  balances?: Record<string, number>;
  cumulative_volume?: number;
  realized_pnl?: number;
}

export interface ApiMarketSnapshotRaw {
  num_assets?: number;
  tokens?: string[];
  reserves?: Record<string, number>;
  prices?: Record<string, number>;
  total_liquidity?: number;
  invariant?: number;
  best_bid?: Record<string, number | null>;
  best_ask?: Record<string, number | null>;
  spread?: Record<string, number>;
  total_depth?: Record<string, number>;
  __type__?: string;
  [k: string]: unknown;
}

export interface ApiRoundSnapshot {
  round?: number;
  timestamp?: number;
  epoch?: number;
  agent_states?: Record<string, ApiAgentStateRaw>;
  market_state?: ApiMarketSnapshotRaw | null;
  all_market_states?: Record<string, ApiMarketSnapshotRaw> | null;
  current_slot?: number | null;
  current_leader?: string | null;
}

export interface ApiStepResponse {
  simulation_id: string;
  run_id?: string | null;
  round: number;
  snapshot: ApiRoundSnapshot;
  is_complete: boolean;
}

export interface ApiSimulationStatus {
  simulation_id: string;
  run_id?: string | null;
  current_round: number;
  is_complete: boolean;
  cancelled?: boolean;
}

export interface ApiAllMarketStates {
  simulation_id: string;
  states: Record<string, ApiMarketSnapshotRaw>;
}

export interface ApiParameterStore {
  params: Record<string, unknown>;
  pending: Array<{
    key: string;
    value: unknown;
    execute_at_round: number;
    proposed_by?: string | number | null;
    proposal_id?: string | null;
  }>;
  history: Array<unknown[]>;
}

export interface ApiViolationsResponse {
  violations: Array<{ round: number; message: string }>;
}

export interface ApiEventResponse {
  events: ApiEventRaw[];
}

// ── Frontend shapes ─────────────────────────────────────────────────────────

export interface RoundDelta {
  round: number;
  timestamp: number;
  isComplete: boolean;
  /** Per-token current prices, ordered by `tokens`. */
  tokens: string[];
  prices: number[];
  reserves: number[];
  totalLiquidity: number;
  /** Volume delta for this round, derived from agent_states cumulative deltas. */
  volumeDelta: number;
  /** Estimated fee delta — backend doesn't expose per-round fees in snapshot, so caller can derive from prior values. */
  totalCumulativeVolume: number;
  /** Per-agent balance totals (balance summed across all tokens), keyed by agent id. */
  agentBalances: Record<string, number>;
  /** Events emitted in this snapshot, if backend includes them. Currently the snapshot doesn't carry events; the runner pulls them via /events. */
  events: EvEntry[];
  /** Slot clock data, populated when the engine runs under a SolanaSlotClock; null otherwise. */
  currentSlot: number | null;
  currentLeader: string | null;
}

export interface ParameterRow {
  key: string;
  value: unknown;
  pendingAtRound?: number;
}

export interface ParameterStoreView {
  rows: ParameterRow[];
  pending: ApiParameterStore["pending"];
  history: Array<{ round: number; key: string; oldValue: unknown; newValue: unknown }>;
}

export interface ViolationRow {
  round: number;
  message: string;
}

// ── Mappers ─────────────────────────────────────────────────────────────────

function summariseTokens(snap: ApiMarketSnapshotRaw | undefined | null): string[] {
  if (!snap) return [];
  if (Array.isArray(snap.tokens) && snap.tokens.length > 0) {
    return snap.tokens.map((t) => String(t));
  }
  if (snap.prices && typeof snap.prices === "object") {
    return Object.keys(snap.prices);
  }
  if (snap.reserves && typeof snap.reserves === "object") {
    return Object.keys(snap.reserves);
  }
  return [];
}

function pickPrices(snap: ApiMarketSnapshotRaw | undefined | null, tokens: string[]): number[] {
  if (!snap) return tokens.map(() => 0);
  if (snap.prices) {
    return tokens.map((t) => Number(snap.prices?.[t] ?? 0));
  }
  if (snap.best_bid && snap.best_ask) {
    return tokens.map((t) => {
      const bid = Number(snap.best_bid?.[t] ?? 0);
      const ask = Number(snap.best_ask?.[t] ?? 0);
      if (bid === 0 && ask === 0) return 0;
      if (bid === 0) return ask;
      if (ask === 0) return bid;
      return (bid + ask) / 2;
    });
  }
  return tokens.map(() => 0);
}

function pickReserves(snap: ApiMarketSnapshotRaw | undefined | null, tokens: string[]): number[] {
  if (!snap) return tokens.map(() => 0);
  if (snap.reserves) {
    return tokens.map((t) => Number(snap.reserves?.[t] ?? 0));
  }
  if (snap.total_depth) {
    return tokens.map((t) => Number(snap.total_depth?.[t] ?? 0));
  }
  return tokens.map(() => 0);
}

function firstMarketSnap(
  snap: ApiRoundSnapshot,
  marketName?: string | null,
): ApiMarketSnapshotRaw | undefined {
  if (snap.market_state) return snap.market_state;
  if (snap.all_market_states) {
    if (marketName) {
      const selected = snap.all_market_states[marketName];
      if (selected) return selected;
    }
    const keys = Object.keys(snap.all_market_states).sort((a, b) => a.localeCompare(b));
    const firstKey = keys[0];
    if (firstKey) return snap.all_market_states[firstKey];
  }
  return undefined;
}

export function marketSeriesFromSnapshot(snap: ApiMarketSnapshotRaw | undefined | null) {
  const tokens = summariseTokens(snap);
  return {
    tokens,
    prices: pickPrices(snap, tokens),
    reserves: pickReserves(snap, tokens),
    totalLiquidity: typeof snap?.total_liquidity === "number" ? snap.total_liquidity : 0,
  };
}

function totalCumulativeVolume(states: Record<string, ApiAgentStateRaw> | undefined): number {
  if (!states) return 0;
  let sum = 0;
  for (const s of Object.values(states)) {
    if (typeof s.cumulative_volume === "number") sum += s.cumulative_volume;
  }
  return sum;
}

function agentBalanceTotals(
  states: Record<string, ApiAgentStateRaw> | undefined,
): Record<string, number> {
  if (!states) return {};
  const out: Record<string, number> = {};
  for (const [id, state] of Object.entries(states)) {
    let total = 0;
    if (state.balances) {
      for (const v of Object.values(state.balances)) {
        if (typeof v === "number") total += v;
      }
    }
    out[id] = total;
  }
  return out;
}

export function fromApiStep(
  resp: ApiStepResponse,
  prior?: { totalCumulativeVolume: number },
  marketName?: string | null,
): RoundDelta {
  const snap = resp.snapshot ?? {};
  const market = firstMarketSnap(snap, marketName);
  const { tokens, prices, reserves, totalLiquidity } = marketSeriesFromSnapshot(market);
  const cumVol = totalCumulativeVolume(snap.agent_states);
  const volumeDelta = prior ? Math.max(0, cumVol - prior.totalCumulativeVolume) : 0;
  const agentBalances = agentBalanceTotals(snap.agent_states);

  return {
    round: resp.round ?? snap.round ?? 0,
    timestamp: snap.timestamp ?? 0,
    isComplete: !!resp.is_complete,
    tokens,
    prices,
    reserves,
    totalLiquidity,
    volumeDelta,
    totalCumulativeVolume: cumVol,
    agentBalances,
    events: [],
    currentSlot: typeof snap.current_slot === "number" ? snap.current_slot : null,
    currentLeader: typeof snap.current_leader === "string" ? snap.current_leader : null,
  };
}

export function fromApiStatus(raw: ApiSimulationStatus) {
  return {
    simulationId: raw.simulation_id,
    runId: raw.run_id ?? null,
    currentRound: raw.current_round ?? 0,
    isComplete: !!raw.is_complete,
    cancelled: !!raw.cancelled,
  };
}

export function fromApiAllMarkets(raw: ApiAllMarketStates) {
  const out: Array<{ name: string; snapshot: ApiMarketSnapshotRaw }> = [];
  for (const [name, snap] of Object.entries(raw.states || {})) {
    out.push({ name, snapshot: snap });
  }
  return out;
}

export function fromApiParameters(raw: ApiParameterStore): ParameterStoreView {
  const rows: ParameterRow[] = Object.entries(raw.params || {}).map(([key, value]) => ({
    key,
    value,
  }));
  for (const p of raw.pending || []) {
    const existing = rows.find((r) => r.key === p.key);
    if (existing) existing.pendingAtRound = p.execute_at_round;
    else
      rows.push({
        key: p.key,
        value: undefined,
        pendingAtRound: p.execute_at_round,
      });
  }
  const history = (raw.history || []).map((entry) => {
    const arr = entry as unknown[];
    return {
      round: typeof arr[0] === "number" ? arr[0] : 0,
      key: typeof arr[1] === "string" ? arr[1] : "",
      oldValue: arr[2],
      newValue: arr[3],
    };
  });
  return { rows, pending: raw.pending || [], history };
}

export function fromApiViolations(raw: ApiViolationsResponse): ViolationRow[] {
  return (raw.violations || []).map((v) => ({
    round: v.round,
    message: v.message,
  }));
}

export function fromApiEngineEvents(raw: ApiEventResponse): EvEntry[] {
  return (raw.events || []).map(fromApiEvent);
}
