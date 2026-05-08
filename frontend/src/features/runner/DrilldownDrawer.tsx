"use client";

import { useEffect, useState } from "react";
import type { EvEntry } from "@/lib/types";
import { apiFetch } from "@/lib/api/client";
import { fromApiEvents, type ApiEventRaw } from "@/lib/api/adapters/runs";
import Skeleton from "@/components/feedback/Skeleton";
import { toToastMessage } from "@/lib/api/errors";

interface DrilldownDrawerProps {
  open: boolean;
  onClose: () => void;
  runId: string;
  event?: EvEntry;
  round?: number;
}

interface DrilldownData {
  events: EvEntry[];
  roundSnapshot: Record<string, unknown> | null;
}

interface ApiRoundSnapshotResponse {
  round?: number;
  snapshot?: Record<string, unknown>;
}

interface ApiEventsResponse {
  events?: ApiEventRaw[];
}

function summariseAgents(snapshot: Record<string, unknown> | null): {
  count: number;
  totalBalance: number;
  totalVolume: number;
} {
  if (!snapshot) return { count: 0, totalBalance: 0, totalVolume: 0 };
  const states = (snapshot.agent_states as Record<string, Record<string, unknown>> | undefined) ?? {};
  let totalBalance = 0;
  let totalVolume = 0;
  for (const state of Object.values(states)) {
    const balances = (state.balances as Record<string, number> | undefined) ?? {};
    for (const v of Object.values(balances)) {
      if (typeof v === "number") totalBalance += v;
    }
    if (typeof state.cumulative_volume === "number") totalVolume += state.cumulative_volume;
  }
  return { count: Object.keys(states).length, totalBalance, totalVolume };
}

// PRD US-014 line 1125: surface ``recent_blockhash`` / ``expiry_slot`` in
// the action inspector. The backend serializes ``ACTION_*`` events with the
// ``Action`` object on ``data.action``; pull the fields out when present so
// fork-stress runs can see why an action expired without leaving the drawer.
function pickActionBlockhash(event: { data?: Record<string, unknown> } | undefined): {
  recent_blockhash: string;
  expiry_slot: number | null;
} | null {
  const action = event?.data?.action;
  if (!action || typeof action !== "object") return null;
  const a = action as Record<string, unknown>;
  const blockhash = a.recent_blockhash;
  if (typeof blockhash !== "string" || blockhash.length === 0) return null;
  const expiry = a.expiry_slot;
  return {
    recent_blockhash: blockhash,
    expiry_slot: typeof expiry === "number" ? expiry : null,
  };
}

function pickPriceSummary(snapshot: Record<string, unknown> | null): {
  tokens: string[];
  prices: number[];
} {
  if (!snapshot) return { tokens: [], prices: [] };
  const market =
    (snapshot.market_state as Record<string, unknown> | undefined) ??
    (() => {
      const all = snapshot.all_market_states as Record<string, Record<string, unknown>> | undefined;
      if (!all) return undefined;
      const first = Object.values(all)[0];
      return first;
    })();
  if (!market) return { tokens: [], prices: [] };
  const prices = (market.prices as Record<string, number> | undefined) ?? {};
  const tokens = Object.keys(prices);
  return { tokens, prices: tokens.map((t) => prices[t] ?? 0) };
}

export default function DrilldownDrawer({
  open,
  onClose,
  runId,
  event,
  round,
}: DrilldownDrawerProps) {
  const [data, setData] = useState<DrilldownData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    if (round === undefined && !event) return;
    const targetRound = event?.round ?? round ?? 0;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    (async () => {
      try {
        const [eventsResp, roundResp] = await Promise.all([
          apiFetch<ApiEventsResponse>(`/runs/${runId}/events`, {
            query: { round: targetRound, limit: 50 },
          }).catch(() => ({ events: [] }) as ApiEventsResponse),
          apiFetch<ApiRoundSnapshotResponse>(
            `/runs/${runId}/rounds/${targetRound}`,
          ).catch(() => null),
        ]);
        if (cancelled) return;
        setData({
          events: fromApiEvents(eventsResp.events ?? []),
          roundSnapshot: roundResp?.snapshot ?? null,
        });
      } catch (err) {
        if (cancelled) return;
        setError(toToastMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, runId, event, round]);

  if (!open) return null;

  const targetRound = event?.round ?? round ?? 0;
  const agentSummary = summariseAgents(data?.roundSnapshot ?? null);
  const priceSummary = pickPriceSummary(data?.roundSnapshot ?? null);
  const blockhashInfo = pickActionBlockhash(event);

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 420,
        background: "var(--bg-1)",
        borderLeft: "1px solid var(--border)",
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        boxShadow: "-4px 0 20px rgba(0,0,0,.3)",
      }}
    >
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h3 style={{ fontSize: ".95rem" }}>
          {event ? `Event @ R${targetRound}` : `Round ${targetRound} Context`}
        </h3>
        <button className="btn-icon" onClick={onClose}>
          <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
            <path
              d="M4 4L14 14M14 4L4 14"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: "16px 20px" }}>
        {event && (
          <div className="form-section">
            <h4>Event</h4>
            <table>
              <tbody>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Round</td>
                  <td className="mono">{event.round}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Type</td>
                  <td className={`ev-type ${event.cls}`} style={{ fontWeight: 600 }}>
                    {event.evType}
                  </td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Detail</td>
                  <td style={{ fontSize: ".82rem" }}>{event.detail}</td>
                </tr>
                {blockhashInfo && (
                  <>
                    <tr>
                      <td style={{ color: "var(--text-2)" }}>Blockhash</td>
                      <td className="mono" style={{ fontSize: ".82rem" }}>
                        {blockhashInfo.recent_blockhash}
                      </td>
                    </tr>
                    {blockhashInfo.expiry_slot !== null && (
                      <tr>
                        <td style={{ color: "var(--text-2)" }}>Expiry slot</td>
                        <td className="mono">{blockhashInfo.expiry_slot}</td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </div>
        )}

        {loading && (
          <div className="form-section">
            <Skeleton height={14} width="60%" />
            <div style={{ marginTop: 8 }}>
              <Skeleton height={12} />
            </div>
            <div style={{ marginTop: 6 }}>
              <Skeleton height={12} width="80%" />
            </div>
          </div>
        )}

        {error && (
          <div className="form-section">
            <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{error}</p>
          </div>
        )}

        {!loading && !error && data && (
          <>
            <div className="form-section">
              <h4>Round {targetRound} Summary</h4>
              <table>
                <tbody>
                  <tr>
                    <td style={{ color: "var(--text-2)" }}>Events in round</td>
                    <td className="mono">{data.events.length}</td>
                  </tr>
                  <tr>
                    <td style={{ color: "var(--text-2)" }}>Agents observed</td>
                    <td className="mono">{agentSummary.count}</td>
                  </tr>
                  <tr>
                    <td style={{ color: "var(--text-2)" }}>Total balance</td>
                    <td className="mono">{agentSummary.totalBalance.toLocaleString()}</td>
                  </tr>
                  <tr>
                    <td style={{ color: "var(--text-2)" }}>Cumulative volume</td>
                    <td className="mono">{agentSummary.totalVolume.toLocaleString()}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {priceSummary.tokens.length > 0 && (
              <div className="form-section">
                <h4>Token Prices</h4>
                <table>
                  <tbody>
                    {priceSummary.tokens.map((tok, i) => (
                      <tr key={tok}>
                        <td style={{ color: "var(--text-2)" }}>{tok}</td>
                        <td className="mono">{priceSummary.prices[i].toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="form-section">
              <h4>Events in round</h4>
              {data.events.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                  No events recorded for this round.
                </p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {data.events.map((ev, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        gap: 8,
                        fontSize: ".82rem",
                        padding: "4px 6px",
                        borderRadius: 4,
                        background: "var(--bg-2)",
                      }}
                    >
                      <span className={`ev-type ${ev.cls}`}>{ev.evType}</span>
                      <span style={{ color: "var(--text-2)" }}>{ev.detail}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
