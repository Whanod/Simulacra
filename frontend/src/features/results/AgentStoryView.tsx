"use client";

import { useState, useMemo } from "react";
import type { AgentRow } from "@/lib/types";
import { hashColorVar } from "@/lib/utils/hashColor";
import { ChartCanvas, TimelineChart } from "@/components/charts";
import Card from "@/components/ui/Card";
import StatCard from "@/components/ui/StatCard";
import Skeleton from "@/components/feedback/Skeleton";
import { useAsync } from "@/lib/hooks/useAsync";
import {
  simulationService,
  type AgentTimelineEntry,
} from "@/lib/services/simulationService";
import { toToastMessage } from "@/lib/api/errors";

interface AgentStoryViewProps {
  runId: string;
  totalRounds: number;
  agent: AgentRow;
  onClose: () => void;
  onJumpToRound?: (round: number) => void;
}

export default function AgentStoryView({
  runId,
  totalRounds,
  agent,
  onClose,
  onJumpToRound,
}: AgentStoryViewProps) {
  const [activeTab, setActiveTab] = useState<"overview" | "trades" | "events">("overview");

  const timelineState = useAsync<AgentTimelineEntry[]>(
    () => simulationService.getAgentTimeline(runId, agent.agentId, { limit: 500 }),
    [runId, agent.agentId],
  );

  const balances = useMemo(
    () => (timelineState.data ?? []).map((e) => e.balance),
    [timelineState.data],
  );
  const pnlSeries = useMemo(
    () => (timelineState.data ?? []).map((e) => e.realizedPnl),
    [timelineState.data],
  );
  const volumeSeries = useMemo(
    () => (timelineState.data ?? []).map((e) => e.cumulativeVolume),
    [timelineState.data],
  );

  // Approximate trade list: per-round volume deltas. Each non-zero delta
  // counts as a trade (BUY if balance went up, SELL otherwise).
  const trades = useMemo(() => {
    const entries = timelineState.data ?? [];
    if (entries.length < 2) return [] as Array<{ round: number; type: "BUY" | "SELL"; amount: number; pnl: number }>;
    const out: Array<{ round: number; type: "BUY" | "SELL"; amount: number; pnl: number }> = [];
    for (let i = 1; i < entries.length; i++) {
      const cur = entries[i];
      const prev = entries[i - 1];
      const dVol = cur.cumulativeVolume - prev.cumulativeVolume;
      if (dVol === 0) continue;
      out.push({
        round: cur.round,
        type: cur.balance >= prev.balance ? "BUY" : "SELL",
        amount: Math.abs(dVol),
        pnl: cur.realizedPnl - prev.realizedPnl,
      });
    }
    return out;
  }, [timelineState.data]);

  const timelineEvents = useMemo(
    () =>
      trades.map((t) => ({
        round: t.round,
        type: t.type,
        color: t.type === "BUY" ? "var(--green)" : "var(--red)",
      })),
    [trades],
  );

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 540,
        background: "var(--bg-1)",
        borderLeft: "1px solid var(--border)",
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        boxShadow: "-4px 0 20px rgba(0,0,0,.3)",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "16px 20px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <h3 style={{ fontSize: ".95rem" }}>{agent.agentId}</h3>
          <span style={{ color: hashColorVar(agent.role), fontSize: ".82rem", fontWeight: 600 }}>
            {agent.role}
          </span>
        </div>
        <button className="btn-icon" onClick={onClose} aria-label="Close agent story">
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

      {/* Tabs */}
      <div className="tabs" style={{ padding: "0 20px" }}>
        {(["overview", "trades", "events"] as const).map((tab) => (
          <button
            key={tab}
            className={activeTab === tab ? "active" : ""}
            onClick={() => setActiveTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px 20px" }}>
        {activeTab === "overview" && (
          <>
            <div className="grid-2" style={{ gap: 8, marginBottom: 16 }}>
              <StatCard label="Final Balance" value={agent.balance.toLocaleString()} valueSize="1rem" />
              <StatCard
                label="PnL"
                value={`${agent.pnl >= 0 ? "+" : ""}${agent.pnl.toFixed(0)}`}
                valueColor={agent.pnl >= 0 ? "var(--green)" : "var(--red)"}
                valueSize="1rem"
              />
              <StatCard label="Volume" value={agent.volume.toLocaleString()} valueSize="1rem" />
              <StatCard label="Trades (derived)" value={trades.length.toString()} valueSize="1rem" />
            </div>

            {timelineState.loading && (
              <Card title="Timeline">
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <Skeleton height={20} />
                  <Skeleton height={140} />
                  <Skeleton height={120} />
                </div>
              </Card>
            )}

            {timelineState.error != null && !timelineState.loading && (
              <Card title="Timeline">
                <p style={{ color: "var(--red)", fontSize: ".82rem" }}>
                  {toToastMessage(timelineState.error)}
                </p>
              </Card>
            )}

            {!timelineState.loading && timelineState.error == null && (
              <>
                <Card title="Balance Over Time">
                  {balances.length === 0 ? (
                    <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                      No timeline rounds recorded.
                    </p>
                  ) : (
                    <ChartCanvas
                      datasets={[
                        {
                          data: balances,
                          color: hashColorVar(agent.role),
                          label: "Balance",
                          fill: true,
                        },
                      ]}
                      decimals={0}
                      height={160}
                      onPointClick={onJumpToRound}
                    />
                  )}
                </Card>

                <Card title="Realized PnL">
                  {pnlSeries.length === 0 ? (
                    <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No PnL data.</p>
                  ) : (
                    <ChartCanvas
                      datasets={[
                        {
                          data: pnlSeries,
                          color: agent.pnl >= 0 ? "#34d399" : "#f87171",
                          label: "PnL",
                          fill: true,
                        },
                      ]}
                      decimals={0}
                      height={140}
                      onPointClick={onJumpToRound}
                    />
                  )}
                </Card>

                <Card title="Cumulative Volume">
                  {volumeSeries.length === 0 ? (
                    <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>No volume data.</p>
                  ) : (
                    <ChartCanvas
                      datasets={[
                        { data: volumeSeries, color: "#fbbf24", label: "Volume", fill: true },
                      ]}
                      decimals={0}
                      height={120}
                      onPointClick={onJumpToRound}
                    />
                  )}
                </Card>

                <Card title="Activity Timeline">
                  <TimelineChart
                    events={timelineEvents}
                    totalRounds={Math.max(totalRounds, 1)}
                    height={50}
                    onRoundClick={onJumpToRound}
                  />
                </Card>
              </>
            )}
          </>
        )}

        {activeTab === "trades" && (
          <div className="table-wrap" style={{ maxHeight: "calc(100vh - 200px)", overflowY: "auto" }}>
            {timelineState.loading ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <Skeleton height={20} />
                <Skeleton height={20} />
                <Skeleton height={20} />
              </div>
            ) : trades.length === 0 ? (
              <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                No volume changes recorded for this agent.
              </p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Round</th>
                    <th>Type</th>
                    <th>Volume Δ</th>
                    <th>PnL Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr
                      key={i}
                      style={{ cursor: onJumpToRound ? "pointer" : undefined }}
                      onClick={() => onJumpToRound?.(t.round)}
                    >
                      <td className="mono">R{t.round}</td>
                      <td
                        style={{
                          color: t.type === "BUY" ? "var(--green)" : "var(--red)",
                          fontWeight: 600,
                        }}
                      >
                        {t.type}
                      </td>
                      <td className="mono">{t.amount.toLocaleString()}</td>
                      <td
                        className="mono"
                        style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}
                      >
                        {t.pnl >= 0 ? "+" : ""}
                        {t.pnl.toFixed(0)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {activeTab === "events" && (
          <div className="event-log" style={{ maxHeight: "calc(100vh - 200px)" }}>
            {trades.length === 0 ? (
              <p style={{ color: "var(--text-2)", fontSize: ".82rem", padding: "8px 12px" }}>
                No activity yet.
              </p>
            ) : (
              trades.slice(0, 100).map((t, i) => (
                <div className="ev" key={i}>
                  <span className="ev-round">R{t.round}</span>
                  <span className="ev-type trade">VOLUME_DELTA</span>
                  <span className="ev-detail">
                    {agent.agentId} {t.type.toLowerCase()} Δvol={t.amount.toLocaleString()}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
