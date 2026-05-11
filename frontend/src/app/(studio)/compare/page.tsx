"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useStudioStore } from "@/lib/state/useStudioStore";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { ChartCanvas } from "@/components/charts";
import { simulationService } from "@/lib/services/simulationService";
import type { CompareView } from "@/lib/api/adapters/compare";
import { useAsync } from "@/lib/hooks/useAsync";
import { ApiError, toToastMessage } from "@/lib/api/errors";
import {
  chartDataFromResult,
  derivedNumericMetrics,
  metricsFromResult,
  type ApiRunResult,
} from "@/lib/api/adapters/runs";
import {
  formatMetricValue,
  parseMetricKey,
  type ParsedMetricKey,
} from "@/lib/api/adapters/metricMeta";
import { formatPnl, pnlDenom } from "@/lib/utils/formatPnl";

function formatValue(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface RecommendedRow {
  key: string;
  label: string;
  hint?: string;
  formatA: string;
  formatB: string;
  delta: number | null;
  deltaLabel: string;
  direction: "better" | "worse" | "neutral";
}

function fmtNumber(value: number, digits = 3): string {
  if (!Number.isFinite(value)) {
    if (value === Number.POSITIVE_INFINITY) return "∞";
    if (value === Number.NEGATIVE_INFINITY) return "-∞";
    return "—";
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function classify(
  dir: "higher" | "lower" | "neutral",
  delta: number,
): RecommendedRow["direction"] {
  if (!Number.isFinite(delta) || delta === 0) return "neutral";
  if (dir === "higher") return delta > 0 ? "better" : "worse";
  if (dir === "lower") return delta < 0 ? "better" : "worse";
  return "neutral";
}

function deltaText(delta: number, digits = 3): string {
  if (!Number.isFinite(delta)) return "—";
  const sign = delta >= 0 ? "+" : "";
  return `${sign}${delta.toLocaleString(undefined, { maximumFractionDigits: digits })}`;
}

function buildRecommendedComparison(
  a: ApiRunResult | null,
  b: ApiRunResult | null,
): RecommendedRow[] {
  if (!a || !b) return [];
  const derivedA = derivedNumericMetrics(a);
  const derivedB = derivedNumericMetrics(b);
  const metricsA = metricsFromResult(a);
  const metricsB = metricsFromResult(b);

  const rows: RecommendedRow[] = [];

  // Engine-emitted derived metrics — union of keys recognized by metricMeta.
  const keys = new Set<string>([...Object.keys(derivedA), ...Object.keys(derivedB)]);
  const parsedKeys: Array<{ key: string; parsed: ParsedMetricKey }> = [];
  for (const key of keys) {
    const parsed = parseMetricKey(key);
    if (parsed === null) continue;
    parsedKeys.push({ key, parsed });
  }
  parsedKeys.sort((x, y) => {
    if (x.parsed.base !== y.parsed.base) return x.parsed.base.localeCompare(y.parsed.base);
    if (!x.parsed.variant && y.parsed.variant) return -1;
    if (x.parsed.variant && !y.parsed.variant) return 1;
    return (x.parsed.variant ?? "").localeCompare(y.parsed.variant ?? "");
  });

  for (const { key, parsed } of parsedKeys) {
    const va = derivedA[key];
    const vb = derivedB[key];
    const hasA = typeof va === "number" && Number.isFinite(va);
    const hasB = typeof vb === "number" && Number.isFinite(vb);
    const delta = hasA && hasB ? vb - va : null;
    rows.push({
      key,
      label: parsed.label,
      hint: parsed.meta.hint,
      formatA: hasA ? formatMetricValue(va, parsed.meta) : "—",
      formatB: hasB ? formatMetricValue(vb, parsed.meta) : "—",
      delta,
      deltaLabel: delta === null ? "—" : deltaText(delta, parsed.meta.digits ?? 3),
      direction: delta === null ? "neutral" : classify(parsed.meta.direction, delta),
    });
  }

  // Client-derived essentials — same tiles the results page renders.
  const lpA = metricsA.lpProfitability;
  const lpB = metricsB.lpProfitability;
  const lpDelta = lpA !== null && lpB !== null ? lpB - lpA : null;
  rows.push({
    key: "lp_fee_yield",
    label: "LP fee yield",
    hint: "1 + fee yield over the run",
    formatA: lpA !== null ? lpA.toFixed(3) : "—",
    formatB: lpB !== null ? lpB.toFixed(3) : "—",
    delta: lpDelta,
    deltaLabel: lpDelta === null ? "—" : deltaText(lpDelta, 3),
    direction: lpDelta === null ? "neutral" : classify("higher", lpDelta),
  });

  const tickDelta = metricsB.tickCrossings - metricsA.tickCrossings;
  rows.push({
    key: "tick_crossings",
    label: "Tick crossings",
    hint: "Initialized ticks consumed by swaps",
    formatA: metricsA.tickCrossings.toLocaleString(),
    formatB: metricsB.tickCrossings.toLocaleString(),
    delta: tickDelta,
    deltaLabel: deltaText(tickDelta, 0),
    direction: classify("neutral", tickDelta),
  });

  const ddDelta = metricsB.maxDrawdown - metricsA.maxDrawdown;
  rows.push({
    key: "max_drawdown_price",
    label: "Max drawdown (price)",
    hint: "Worst peak-to-trough on the spot price",
    formatA: `${metricsA.maxDrawdown.toFixed(2)}%`,
    formatB: `${metricsB.maxDrawdown.toFixed(2)}%`,
    delta: ddDelta,
    deltaLabel: `${deltaText(ddDelta, 2)}%`,
    direction: classify("higher", ddDelta), // less-negative is better → higher delta is better
  });

  const volDelta = metricsB.rollingVol - metricsA.rollingVol;
  rows.push({
    key: "rolling_vol",
    label: "Rolling volatility",
    hint: "20-round window",
    formatA: metricsA.rollingVol.toFixed(4),
    formatB: metricsB.rollingVol.toFixed(4),
    delta: volDelta,
    deltaLabel: deltaText(volDelta, 4),
    direction: classify("lower", volDelta),
  });

  // Composite + stress (always derivable from the result; nice to compare).
  const compDelta = metricsB.compositeScore - metricsA.compositeScore;
  rows.push({
    key: "composite_score",
    label: "Composite score",
    hint: "Heuristic blend of drawdown, vol, LP profitability",
    formatA: fmtNumber(metricsA.compositeScore, 0),
    formatB: fmtNumber(metricsB.compositeScore, 0),
    delta: compDelta,
    deltaLabel: deltaText(compDelta, 0),
    direction: classify("higher", compDelta),
  });

  const stressDelta = metricsB.stressScore - metricsA.stressScore;
  rows.push({
    key: "stress_score",
    label: "Manipulation stress",
    hint: "Sandwich pressure score",
    formatA: fmtNumber(metricsA.stressScore, 0),
    formatB: fmtNumber(metricsB.stressScore, 0),
    delta: stressDelta,
    deltaLabel: deltaText(stressDelta, 0),
    direction: classify("lower", stressDelta),
  });

  return rows;
}

function RecommendedCompareCard({ row }: { row: RecommendedRow }) {
  const color =
    row.direction === "better"
      ? "var(--green)"
      : row.direction === "worse"
        ? "var(--red)"
        : "var(--text-2)";
  const arrow =
    row.direction === "better" ? "↑" : row.direction === "worse" ? "↓" : "—";
  return (
    <div className="stat-card">
      <span className="label">{row.label}</span>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <span className="mono" style={{ fontSize: ".95rem" }}>
          {row.formatA}
        </span>
        <span style={{ color: "var(--text-2)", fontSize: ".75rem" }}>vs</span>
        <span className="mono" style={{ fontSize: ".95rem" }}>
          {row.formatB}
        </span>
      </div>
      <span className="delta" style={{ color }}>
        {row.deltaLabel} {arrow}
      </span>
      {row.hint && <span className="hint">{row.hint}</span>}
    </div>
  );
}

export default function ComparePage() {
  const { runs, compareTargets, toggleCompareTarget, refreshRuns } = useStudioStore();
  const router = useRouter();
  const searchParams = useSearchParams();

  // Pick up runs created in other tabs/pages (e.g. a Build & Run from the
  // Builder) without forcing a full reload.
  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  // One-shot hydration from `?a=X&b=Y`: on first render, if the URL has query
  // params that differ from the zustand compareTargets, adopt them. After
  // that we push changes the other direction (state → URL) to keep the deep
  // link current without fighting the user's clicks.
  const [isHydrated, setIsHydrated] = useState(false);
  useEffect(() => {
    if (isHydrated) return;
    const a = searchParams.get("a");
    const b = searchParams.get("b");
    const urlTargets = [a, b].filter((x): x is string => !!x);
    if (urlTargets.length > 0) {
      const current = new Set(compareTargets);
      const desired = new Set(urlTargets);
      const same =
        current.size === desired.size &&
        [...current].every((id) => desired.has(id));
      if (!same) {
        // Walk through: clear then set the URL targets via toggleCompareTarget.
        for (const id of compareTargets) {
          if (!desired.has(id)) toggleCompareTarget(id);
        }
        for (const id of urlTargets) {
          if (!current.has(id)) toggleCompareTarget(id);
        }
      }
    }
    setIsHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHydrated]);

  // Reflect compareTargets into the URL so deep-links stay fresh.
  useEffect(() => {
    if (!isHydrated) return;
    const params = new URLSearchParams(searchParams.toString());
    const [a, b] = compareTargets;
    if (a) params.set("a", a);
    else params.delete("a");
    if (b) params.set("b", b);
    else params.delete("b");
    const qs = params.toString();
    const next = qs ? `/compare?${qs}` : "/compare";
    router.replace(next, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compareTargets, isHydrated]);

  const leftId = compareTargets[0];
  const rightId = compareTargets[1];

  const runA = runs.find((r) => r.id === leftId);
  const runB = runs.find((r) => r.id === rightId);

  const ready = !!leftId && !!rightId;

  // ── Backend comparison ────────────────────────────────
  const compareState = useAsync<CompareView | null>(
    async () => {
      if (!ready) return null;
      return simulationService.compareRuns(leftId!, rightId!);
    },
    [leftId, rightId, ready],
  );

  // ── Per-run results (drives chart overlay + recommended-metrics cards) ──
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [resultA, setResultA] = useState<ApiRunResult | null>(null);
  const [resultB, setResultB] = useState<ApiRunResult | null>(null);

  useEffect(() => {
    if (!ready) {
      setResultA(null);
      setResultB(null);
      setChartError(null);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    (async () => {
      try {
        const [a, b] = await Promise.all([
          simulationService.getResult(leftId!),
          simulationService.getResult(rightId!),
        ]);
        if (cancelled) return;
        setResultA(a);
        setResultB(b);
      } catch (err) {
        if (cancelled) return;
        setChartError(toToastMessage(err));
      } finally {
        if (!cancelled) setChartLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [leftId, rightId, ready]);

  const seriesA = useMemo(
    () => (resultA ? chartDataFromResult(resultA).priceData[0] ?? [] : []),
    [resultA],
  );
  const seriesB = useMemo(
    () => (resultB ? chartDataFromResult(resultB).priceData[0] ?? [] : []),
    [resultB],
  );

  const recommendedComparison = useMemo(
    () => buildRecommendedComparison(resultA, resultB),
    [resultA, resultB],
  );

  const view = compareState.data ?? null;
  const sameSeed = runA && runB && runA.seed === runB.seed;

  const leftMarket = runA?.spec.market;
  const rightMarket = runB?.spec.market;
  const leftDenom = pnlDenom(leftMarket, resultA);
  const rightDenom = pnlDenom(rightMarket, resultB);
  const denomMatch = leftDenom === rightDenom;

  const visibleAgents = useMemo(
    () => view?.agentSummary.slice(0, 12) ?? [],
    [view?.agentSummary],
  );

  return (
    <>
      <Topbar title="Compare Runs" />
      <div id="content" className="fade-in">
        {/* Run Selection */}
        <Card title="Select Runs to Compare">
          {runs.length === 0 ? (
            <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
              No runs available yet. Start one from the Builder.
            </p>
          ) : (
            <div className="grid-3" style={{ marginBottom: 16 }}>
              {runs.map((run) => {
                const selected = compareTargets.includes(run.id);
                return (
                  <div
                    key={run.id}
                    className="card"
                    style={{
                      cursor: "pointer",
                      borderColor: selected ? "var(--accent)" : undefined,
                      background: selected ? "var(--accent-dim)" : undefined,
                    }}
                    onClick={() => toggleCompareTarget(run.id)}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: 8,
                      }}
                    >
                      <span className="mono" style={{ color: "var(--accent)", fontSize: ".85rem" }}>
                        {run.id}
                      </span>
                      <Badge
                        variant={
                          run.status === "completed"
                            ? "green"
                            : run.status === "running"
                              ? "yellow"
                              : "blue"
                        }
                      >
                        {run.status}
                      </Badge>
                    </div>
                    <div style={{ fontSize: ".82rem", color: "var(--text-2)" }}>
                      {run.market} · {run.agents} agents · seed {run.seed} ·{" "}
                      {run.currentRound}/{run.totalRounds}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <p className="hint">
            Selected: {compareTargets.length}/2 runs
            {compareTargets.length === 2 && " — comparison ready"}
            {sameSeed ? " · ⚠ same seed (deltas may be all-zero)" : ""}
          </p>
        </Card>

        {!ready && (
          <Card>
            <div style={{ textAlign: "center", padding: "40px 0", color: "var(--text-2)" }}>
              <p style={{ fontSize: "1.1rem", marginBottom: 8 }}>
                Select two runs above to compare
              </p>
              <p style={{ fontSize: ".85rem" }}>
                You can also start a comparison from the Dashboard or Results pages
              </p>
            </div>
          </Card>
        )}

        {ready && compareState.loading && (
          <Card title="Loading comparison">
            <Skeleton height={20} width="40%" />
            <div style={{ marginTop: 12 }}>
              <Skeleton height={12} />
            </div>
            <div style={{ marginTop: 6 }}>
              <Skeleton height={12} width="80%" />
            </div>
          </Card>
        )}

        {ready && !compareState.loading && compareState.error != null && (
          <Card title="Comparison failed">
            <p style={{ color: "var(--red)", fontSize: ".85rem" }}>
              {compareState.error instanceof ApiError && compareState.error.status === 404
                ? "One or both runs not found."
                : toToastMessage(compareState.error)}
            </p>
          </Card>
        )}

        {ready && view && !compareState.loading && (
          <>
            {/* Spec Diff */}
            <Card title={`Spec Differences (${view.specDiff.length})`}>
              {view.specDiff.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  Specs are identical at every key.
                </p>
              ) : (
                <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Path</th>
                        <th>{view.leftRunId.slice(0, 12)}…</th>
                        <th>{view.rightRunId.slice(0, 12)}…</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.specDiff.map((row) => (
                        <tr key={row.key}>
                          <td className="mono" style={{ color: "var(--text-2)" }}>
                            {row.key}
                          </td>
                          <td className="mono">{formatValue(row.left)}</td>
                          <td className="mono">{formatValue(row.right)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            {/* Recommended metrics comparison — mirrors the cards on the
                Results page (engine-emitted derived_metrics + LP fee yield,
                tick crossings, drawdown, vol, composite, stress) but shows
                both runs side-by-side with a delta. Distinct titled card so
                it reads as its own section, separate from the raw metric
                deltas below. */}
            <Card title="Recommended Metrics Comparison">
              {chartLoading && recommendedComparison.length === 0 ? (
                <Skeleton height={120} />
              ) : recommendedComparison.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                  No recommended metrics available for these runs.
                </p>
              ) : (
                <>
                  <p
                    className="hint"
                    style={{ marginBottom: 10, color: "var(--text-2)", fontSize: ".78rem" }}
                  >
                    Left ({view.leftRunId.slice(0, 8)}) vs Right (
                    {view.rightRunId.slice(0, 8)}) — Δ = Right − Left.
                  </p>
                  <div className="grid-4">
                    {recommendedComparison.map((row) => (
                      <RecommendedCompareCard key={row.key} row={row} />
                    ))}
                  </div>
                </>
              )}
            </Card>

            {/* Run Metric Deltas — raw ``metric_diff`` keys from the
                backend (num_rounds, seed, cancelled, etc.). Wrapped in its
                own titled card so it doesn't float loose under the
                Recommended Metrics card. */}
            {view.metricDeltas.length > 0 && (
              <Card title="Run Metric Deltas">
                <div className="grid-4">
                  {view.metricDeltas.map((m) => (
                    <div key={m.metric} className="stat-card">
                      <span className="label">{m.metric}</span>
                      <div style={{ display: "flex", gap: 16, alignItems: "baseline" }}>
                        <span className="mono" style={{ fontSize: ".85rem" }}>
                          {Number.isFinite(m.valueA) ? m.valueA : "—"}
                        </span>
                        <span style={{ color: "var(--text-2)" }}>vs</span>
                        <span className="mono" style={{ fontSize: ".85rem" }}>
                          {Number.isFinite(m.valueB) ? m.valueB : "—"}
                        </span>
                      </div>
                      <span
                        className="delta"
                        style={{
                          color:
                            m.direction === "better"
                              ? "var(--green)"
                              : m.direction === "worse"
                                ? "var(--red)"
                                : "var(--text-2)",
                        }}
                      >
                        {m.delta >= 0 ? "+" : ""}
                        {m.delta.toFixed(3)}{" "}
                        {m.direction === "better" ? "↑" : m.direction === "worse" ? "↓" : "—"}
                      </span>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Price overlay */}
            <Card title="Price Comparison (Overlay)">
              {chartLoading ? (
                <Skeleton height={240} />
              ) : chartError ? (
                <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{chartError}</p>
              ) : seriesA.length === 0 && seriesB.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                  No price history available for either run.
                </p>
              ) : (
                <ChartCanvas
                  height={260}
                  datasets={[
                    {
                      data: seriesA,
                      color: "#6c8aff",
                      label: `${view.leftRunId.slice(0, 8)}`,
                      width: 2,
                    },
                    {
                      data: seriesB,
                      color: "#f87171",
                      label: `${view.rightRunId.slice(0, 8)}`,
                      width: 2,
                    },
                  ]}
                />
              )}
            </Card>

            {/* Price summary */}
            {view.priceSummary.length > 0 && (
              <Card
                title={`Price Endpoint Deltas${
                  denomMatch ? ` (priced in ${leftDenom})` : ""
                }`}
              >
                {!denomMatch && (
                  <p
                    style={{
                      color: "var(--text-2)",
                      fontSize: ".78rem",
                      marginBottom: 8,
                    }}
                  >
                    Prices are quoted per-run in each run&apos;s collateral token
                    (left: <span className="mono">{leftDenom}</span>, right:{" "}
                    <span className="mono">{rightDenom}</span>).
                  </p>
                )}
                <div className="table-wrap" style={{ maxHeight: 220, overflowY: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Token</th>
                        <th
                          title={`End-of-run spot price, quoted in ${leftDenom}`}
                        >
                          Left end ({leftDenom})
                        </th>
                        <th
                          title={`End-of-run spot price, quoted in ${rightDenom}`}
                        >
                          Right end ({rightDenom})
                        </th>
                        <th
                          title={
                            denomMatch
                              ? `Right − Left, in ${leftDenom}`
                              : "Hidden — runs use different quote denominations"
                          }
                        >
                          Δ end{denomMatch ? ` (${leftDenom})` : ""}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.priceSummary.map((row) => (
                        <tr key={row.key}>
                          <td className="mono">{row.key}</td>
                          <td className="mono">{row.leftEnd?.toFixed(4) ?? "—"}</td>
                          <td className="mono">{row.rightEnd?.toFixed(4) ?? "—"}</td>
                          <td
                            className="mono"
                            style={{
                              color:
                                row.deltaEnd && row.deltaEnd > 0
                                  ? "var(--green)"
                                  : row.deltaEnd && row.deltaEnd < 0
                                    ? "var(--red)"
                                    : undefined,
                            }}
                          >
                            {row.deltaEnd !== undefined
                              ? `${row.deltaEnd >= 0 ? "+" : ""}${row.deltaEnd.toFixed(4)}`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            {/* Agent PnL deltas */}
            {visibleAgents.length > 0 && (
              <Card title={`Agent PnL Deltas (top ${visibleAgents.length}/${view.agentSummary.length})`}>
                {!denomMatch && (
                  <p
                    style={{
                      color: "var(--text-2)",
                      fontSize: ".78rem",
                      marginBottom: 8,
                    }}
                  >
                    ⚠ Runs use different collateral denominations (left:{" "}
                    <span className="mono">{leftDenom}</span>, right:{" "}
                    <span className="mono">{rightDenom}</span>). The Δ column is
                    hidden because subtracting incompatible units is meaningless.
                  </p>
                )}
                <div className="table-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Agent</th>
                        <th title={`Realized PnL on left run, denominated in ${leftDenom}`}>
                          Left PnL ({leftDenom})
                        </th>
                        <th title={`Realized PnL on right run, denominated in ${rightDenom}`}>
                          Right PnL ({rightDenom})
                        </th>
                        <th
                          title={
                            denomMatch
                              ? `Right − Left, denominated in ${leftDenom}`
                              : "Hidden — runs use different denominations"
                          }
                        >
                          Δ PnL{denomMatch ? ` (${leftDenom})` : ""}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleAgents.map((row) => (
                        <tr key={row.agentId}>
                          <td className="mono">{row.agentId}</td>
                          <td
                            className="mono"
                            style={{
                              color:
                                (row.leftPnl ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                            }}
                          >
                            {formatPnl(row.leftPnl, leftMarket, { result: resultA })}
                          </td>
                          <td
                            className="mono"
                            style={{
                              color:
                                (row.rightPnl ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                            }}
                          >
                            {formatPnl(row.rightPnl, rightMarket, { result: resultB })}
                          </td>
                          <td
                            className="mono"
                            style={{
                              color:
                                !denomMatch
                                  ? "var(--text-2)"
                                  : (row.deltaPnl ?? 0) > 0
                                    ? "var(--green)"
                                    : (row.deltaPnl ?? 0) < 0
                                      ? "var(--red)"
                                      : undefined,
                            }}
                          >
                            {denomMatch
                              ? formatPnl(row.deltaPnl, leftMarket, { result: resultA })
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    </>
  );
}
