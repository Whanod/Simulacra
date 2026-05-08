"use client";

import Link from "next/link";
import { useMemo } from "react";
import Topbar from "@/components/shell/Topbar";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import EmptyState from "@/components/feedback/EmptyState";
import { useAsync } from "@/lib/hooks/useAsync";
import { toToastMessage } from "@/lib/api/errors";
import {
  calibrationService,
  type CalibrationCorpus,
  type CalibrationCorpusSlot,
  type CalibrationThreshold,
  type CalibrationTrend,
  type TrendDirection,
} from "@/lib/services/calibrationService";

function formatThreshold(t: CalibrationThreshold): string {
  if (t.thresholdRelative !== null) {
    return `±${(t.thresholdRelative * 100).toFixed(2)}% rel.`;
  }
  if (t.thresholdAbsolute !== null) {
    return `±${t.thresholdAbsolute} abs.`;
  }
  return "—";
}

function trendMarker(direction: TrendDirection): { label: string; color: string } {
  if (direction === "improving") return { label: "↓ improving", color: "var(--green)" };
  if (direction === "regressing") return { label: "↑ regressing", color: "var(--red)" };
  if (direction === "stable") return { label: "= stable", color: "var(--text-2)" };
  return { label: "— no history", color: "var(--text-2)" };
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function SlotCard({ slot }: { slot: CalibrationCorpusSlot }) {
  const last = slot.lastRun;
  return (
    <div
      className="card"
      data-testid="calibration-slot-card"
      data-slot={slot.slot}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0 }}>Slot {slot.slot}</h3>
        <span
          data-testid="calibration-slot-run-count"
          style={{ fontSize: ".78rem", color: "var(--text-2)" }}
        >
          {slot.runCount} run{slot.runCount === 1 ? "" : "s"}
        </span>
      </div>

      <div
        style={{
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          marginBottom: 10,
        }}
      >
        {slot.programs.map((p) => (
          <Badge key={p} variant="blue">
            {p.slice(0, 8)}…
          </Badge>
        ))}
        {slot.programs.length === 0 && (
          <span style={{ fontSize: ".78rem", color: "var(--text-2)" }}>
            no programs declared
          </span>
        )}
        <Link
          className="btn btn-secondary btn-sm"
          data-testid="calibration-benchmark-link"
          href={`/benchmark/${slot.slot}`}
          style={{ marginLeft: "auto" }}
        >
          Open benchmark
        </Link>
      </div>

      <div
        data-testid="calibration-slot-last-run"
        style={{
          fontSize: ".82rem",
          color: "var(--text-2)",
          marginBottom: 10,
        }}
      >
        <strong style={{ color: "var(--text)" }}>Last run:</strong>{" "}
        {last ? (
          <>
            <span data-testid="calibration-slot-last-run-timestamp">
              {formatTimestamp(last.createdAt)}
            </span>{" "}
            {last.runId && (
              <span className="mono" style={{ marginLeft: 6 }}>
                ({last.runId.slice(0, 8)}…)
              </span>
            )}
            {last.mainnetAccuracyClaim === true && (
              <Badge variant="green">mainnet-accuracy</Badge>
            )}
            {last.mainnetAccuracyClaim === false && (
              <Badge variant="yellow">synthetic/partial</Badge>
            )}
          </>
        ) : (
          <span>no calibration replay recorded yet</span>
        )}
      </div>

      <div data-testid="calibration-slot-trend">
        <strong style={{ fontSize: ".82rem" }}>Per-metric trend:</strong>
        {slot.trend.length === 0 ? (
          <p
            style={{
              fontSize: ".78rem",
              color: "var(--text-2)",
              margin: "4px 0 0",
            }}
          >
            No metric history. Run a replay against this slot to populate the
            scoreboard.
          </p>
        ) : (
          <ul
            style={{
              listStyle: "none",
              padding: 0,
              margin: "4px 0 0",
              fontSize: ".82rem",
            }}
          >
            {slot.trend.map((t: CalibrationTrend) => {
              const marker = trendMarker(t.direction);
              return (
                <li
                  key={t.metric}
                  data-testid="calibration-trend-row"
                  data-metric={t.metric}
                  data-direction={t.direction}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "2px 0",
                  }}
                >
                  <span className="mono">{t.metric}</span>
                  <span style={{ color: marker.color }}>{marker.label}</span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

export default function CalibrationPage() {
  const corpusState = useAsync<CalibrationCorpus>(
    () => calibrationService.getCorpus(),
    [],
  );

  const summary = useMemo(() => {
    const data = corpusState.data;
    if (!data) return null;
    const slotsWithRuns = data.slots.filter((s) => s.runCount > 0).length;
    const totalSlots = data.slots.length;
    const regressing = data.slots.flatMap((s) =>
      s.trend.filter((t) => t.direction === "regressing"),
    ).length;
    return { slotsWithRuns, totalSlots, regressing };
  }, [corpusState.data]);

  return (
    <>
      <Topbar title="Calibration" />
      <div id="content" className="fade-in" data-testid="calibration-dashboard">
        {corpusState.loading && (
          <div className="grid-3" style={{ marginBottom: 20 }}>
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="card">
                <Skeleton height={18} width="60%" />
                <div style={{ marginTop: 8 }}>
                  <Skeleton height={12} />
                </div>
              </div>
            ))}
          </div>
        )}

        {!corpusState.loading && corpusState.error != null && (
          <EmptyState
            title="Failed to load calibration corpus"
            description={toToastMessage(corpusState.error)}
            action={
              <button
                className="btn btn-secondary btn-sm"
                onClick={corpusState.refetch}
              >
                Retry
              </button>
            }
          />
        )}

        {!corpusState.loading && !corpusState.error && corpusState.data && (
          <>
            {summary && (
              <div className="grid-3" style={{ marginBottom: 20 }}>
                <div className="card" data-testid="calibration-summary-coverage">
                  <div
                    style={{ fontSize: ".75rem", color: "var(--text-2)" }}
                  >
                    Corpus coverage
                  </div>
                  <div
                    style={{
                      fontSize: "1.4rem",
                      fontWeight: 600,
                      marginTop: 4,
                    }}
                  >
                    {summary.slotsWithRuns} / {summary.totalSlots} slots replayed
                  </div>
                </div>
                <div className="card" data-testid="calibration-summary-regressions">
                  <div
                    style={{ fontSize: ".75rem", color: "var(--text-2)" }}
                  >
                    Regressing metrics
                  </div>
                  <div
                    style={{
                      fontSize: "1.4rem",
                      fontWeight: 600,
                      marginTop: 4,
                      color:
                        summary.regressing > 0 ? "var(--red)" : "var(--text)",
                    }}
                  >
                    {summary.regressing}
                  </div>
                </div>
                <div className="card" data-testid="calibration-summary-thresholds">
                  <div
                    style={{ fontSize: ".75rem", color: "var(--text-2)" }}
                  >
                    Threshold metrics
                  </div>
                  <div
                    style={{
                      fontSize: "1.4rem",
                      fontWeight: 600,
                      marginTop: 4,
                    }}
                  >
                    {corpusState.data.thresholds.length}
                  </div>
                </div>
              </div>
            )}

            <Card title="Per-metric thresholds" style={{ marginBottom: 20 }}>
              <table data-testid="calibration-thresholds-table">
                <thead>
                  <tr>
                    <th style={{ textAlign: "left" }}>Metric</th>
                    <th style={{ textAlign: "left" }}>Bound</th>
                  </tr>
                </thead>
                <tbody>
                  {corpusState.data.thresholds.map((t) => (
                    <tr
                      key={t.metric}
                      data-testid="calibration-threshold-row"
                      data-metric={t.metric}
                    >
                      <td className="mono">{t.metric}</td>
                      <td>{formatThreshold(t)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="Per-slot scoreboard">
              {corpusState.data.slots.length === 0 ? (
                <EmptyState
                  title="No corpus slots committed"
                  description="Add slot directories under solana-plans/calibration/corpus/ to populate the dashboard."
                />
              ) : (
                <div className="grid-3">
                  {corpusState.data.slots.map((slot) => (
                    <SlotCard key={slot.slot} slot={slot} />
                  ))}
                </div>
              )}
            </Card>
          </>
        )}
      </div>
    </>
  );
}
