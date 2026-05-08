"use client";

import { use, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Card from "@/components/ui/Card";
import StatCard from "@/components/ui/StatCard";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { ChartCanvas, HeatmapChart } from "@/components/charts";
import { sweepService } from "@/lib/services/sweepService";
import type { SensitivityRow } from "@/lib/services/sweepService";
import { useAsync } from "@/lib/hooks/useAsync";
import type { SweepResult, SweepRun } from "@/lib/types";

interface RobustnessRow {
  key: string;
  params: Record<string, number>;
  mean: number;
  std: number;
  min: number;
  max: number;
  count: number;
}

function numericValue(row: Record<string, unknown>, key: string): number | undefined {
  const v = row[key];
  return typeof v === "number" ? v : undefined;
}

function computeRobustness(
  rows: Record<string, unknown>[],
  paramNames: string[],
  metric: string,
): RobustnessRow[] {
  if (paramNames.length === 0 || rows.length === 0) return [];
  const groups = new Map<string, Record<string, unknown>[]>();
  for (const row of rows) {
    const keyParts = paramNames.map((p) => String(row[p] ?? ""));
    const key = keyParts.join("|");
    const arr = groups.get(key) ?? [];
    arr.push(row);
    groups.set(key, arr);
  }
  const out: RobustnessRow[] = [];
  for (const [key, group] of groups) {
    const params: Record<string, number> = {};
    for (const p of paramNames) {
      const v = numericValue(group[0], p);
      if (v !== undefined) params[p] = v;
    }
    const values = group
      .map((r) => numericValue(r, metric))
      .filter((v): v is number => v !== undefined);
    if (values.length === 0) continue;
    const mean = values.reduce((s, v) => s + v, 0) / values.length;
    const variance =
      values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length;
    const std = Math.sqrt(variance);
    out.push({
      key,
      params,
      mean,
      std,
      min: Math.min(...values),
      max: Math.max(...values),
      count: values.length,
    });
  }
  out.sort((a, b) => b.mean - a.mean);
  return out.slice(0, 8);
}

export default function SweepDetailPage({
  params,
}: {
  params: Promise<{ sweepId: string }>;
}) {
  const { sweepId } = use(params);
  const router = useRouter();
  const { showToast } = useToast();

  const sweepState = useAsync<SweepRun | undefined>(
    () => sweepService.getSweep(sweepId),
    [sweepId],
  );

  const sweep = sweepState.data;
  const paramNames = sweep?.paramNames ?? [];
  const metricNames = sweep?.metricNames ?? [];

  const [sensParam, setSensParam] = useState<string>("");
  const [sensMetric, setSensMetric] = useState<string>("");

  const effectiveSensParam = sensParam || paramNames[0] || "";
  const effectiveSensMetric =
    sensMetric || sweep?.config.targetMetric || metricNames[0] || "";

  const sensitivityState = useAsync<SensitivityRow[]>(
    async () => {
      if (!sweep || !effectiveSensParam || !effectiveSensMetric) return [];
      return sweepService.getSensitivity(
        sweepId,
        effectiveSensParam,
        effectiveSensMetric,
      );
    },
    [sweepId, sweep?.id, effectiveSensParam, effectiveSensMetric],
  );

  const robustness = useMemo(() => {
    if (!sweep) return [];
    return computeRobustness(
      sweep.rows,
      sweep.paramNames,
      sweep.config.targetMetric,
    );
  }, [sweep]);

  const handlePromote = (result: SweepResult) => {
    try {
      sessionStorage.setItem(
        "builder-seed-params",
        JSON.stringify(result.params),
      );
      showToast(`Config #${result.rank} params staged for builder`, "success");
      router.push("/builder");
    } catch {
      showToast("Could not stage params for builder", "error");
    }
  };

  if (sweepState.loading) {
    return (
      <>
        <Topbar title="Loading sweep…" />
        <div id="content" className="fade-in">
          <Skeleton height={24} width="40%" />
          <div style={{ marginTop: 16 }}>
            <Skeleton height={180} />
          </div>
        </div>
      </>
    );
  }

  if (sweepState.error != null || !sweep) {
    return (
      <>
        <Topbar title="Sweep Not Found" />
        <div
          id="content"
          className="fade-in"
          style={{ textAlign: "center", padding: "60px 0" }}
        >
          <p style={{ color: "var(--text-2)", marginBottom: 16 }}>
            Sweep{" "}
            <span className="mono" style={{ color: "var(--accent)" }}>
              {sweepId}
            </span>{" "}
            {sweepState.error != null ? "failed to load." : "not found."}
          </p>
          <button
            className="btn btn-primary"
            onClick={() => router.push("/sweeps")}
          >
            Back to Sweeps
          </button>
        </div>
      </>
    );
  }

  const sensitivityData = sensitivityState.data ?? [];
  const sensitivitySeries = sensitivityData.map((r) => r.mean);

  return (
    <>
      <Topbar title={sweep.name} />
      <div id="content" className="fade-in">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 20,
          }}
        >
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => router.push("/sweeps")}
          >
            &larr; All Sweeps
          </button>
          <span
            className="mono"
            style={{ color: "var(--text-2)", fontSize: ".82rem" }}
          >
            {sweepId}
          </span>
          <Badge
            variant={
              sweep.status === "completed"
                ? "green"
                : sweep.status === "running"
                  ? "yellow"
                  : "blue"
            }
          >
            {sweep.status}
          </Badge>
        </div>

        <div className="grid-4" style={{ marginBottom: 20 }}>
          <StatCard label="Total Runs" value={sweep.totalRuns} />
          <StatCard
            label="Completed"
            value={sweep.completedRuns}
            delta={
              sweep.totalRuns > 0
                ? `${Math.round((sweep.completedRuns / sweep.totalRuns) * 100)}%`
                : "0%"
            }
            deltaDir="up"
          />
          <StatCard
            label="Target Metric"
            value={sweep.config.targetMetric || "—"}
            valueSize="1rem"
          />
          <StatCard
            label="Seeds"
            value={sweep.config.seeds.length}
            hint={sweep.config.seeds.join(", ")}
          />
        </div>

        <div className="grid-2">
          {/* Left column */}
          <div>
            <Card
              title="Heatmap"
              badge={
                paramNames.length > 0 ? (
                  <Badge variant="purple">
                    {paramNames.slice(0, 2).join(" × ")}
                  </Badge>
                ) : null
              }
            >
              {sweep.heatmap.length > 0 ? (
                <div data-testid="sweep-heatmap">
                  <HeatmapChart
                    data={sweep.heatmap}
                    rowLabels={sweep.heatmapRowLabels}
                    colLabels={sweep.heatmapColLabels}
                    valueLabel={sweep.config.targetMetric || "value"}
                  />
                </div>
              ) : (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  Not enough data to render a heatmap.
                </p>
              )}
            </Card>

            <Card
              title="Sensitivity Analysis"
              badge={
                paramNames.length > 0 ? (
                  <Badge variant="blue">
                    {effectiveSensMetric} vs {effectiveSensParam}
                  </Badge>
                ) : null
              }
            >
              {paramNames.length > 0 && (
                <div className="form-row" style={{ marginBottom: 8 }}>
                  <div className="form-group">
                    <label>Parameter</label>
                    <select
                      value={effectiveSensParam}
                      onChange={(e) => setSensParam(e.target.value)}
                    >
                      {paramNames.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Metric</label>
                    <select
                      value={effectiveSensMetric}
                      onChange={(e) => setSensMetric(e.target.value)}
                    >
                      {(metricNames.length > 0
                        ? metricNames
                        : [sweep.config.targetMetric]
                      ).map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}
              {sensitivityState.loading && <Skeleton height={180} />}
              {!sensitivityState.loading && sensitivitySeries.length > 0 && (
                <div data-testid="sweep-sensitivity">
                  <ChartCanvas
                    height={200}
                    datasets={[
                      {
                        data: sensitivitySeries,
                        color: "#6c8aff",
                        fill: true,
                        label: effectiveSensMetric,
                        width: 2,
                      },
                    ]}
                    decimals={3}
                  />
                  <p className="hint" style={{ marginTop: 8 }}>
                    Mean {effectiveSensMetric} across levels of{" "}
                    {effectiveSensParam} (
                    {sensitivityData.map((r) => r.value).join(", ")})
                  </p>
                </div>
              )}
              {!sensitivityState.loading && sensitivitySeries.length === 0 && (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  No sensitivity data available.
                </p>
              )}
            </Card>
          </div>

          {/* Right column */}
          <div>
            <Card
              title="Top Configurations"
              badge={<Badge variant="green">by {sweep.config.direction}</Badge>}
            >
              {sweep.results.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  No results yet.
                </p>
              ) : (
                <div
                  className="table-wrap"
                  style={{ maxHeight: 280, overflowY: "auto" }}
                  data-testid="sweep-top-configs"
                >
                  <table>
                    <thead>
                      <tr>
                        <th>#</th>
                        {paramNames.map((p) => (
                          <th key={p}>{p}</th>
                        ))}
                        <th>{sweep.config.targetMetric}</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sweep.results.map((c) => (
                        <tr key={c.rank}>
                          <td>{c.rank}</td>
                          {paramNames.map((p) => (
                            <td key={p} className="mono">
                              {c.params[p] !== undefined
                                ? Number(c.params[p]).toFixed(3)
                                : "—"}
                            </td>
                          ))}
                          <td className="mono">{c.score.toFixed(3)}</td>
                          <td>
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => handlePromote(c)}
                            >
                              Promote
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <Card title="Robustness Summary">
              {robustness.length === 0 ? (
                <p style={{ color: "var(--text-2)", fontSize: ".85rem" }}>
                  Not enough rows to compute robustness.
                </p>
              ) : (
                <table data-testid="sweep-robustness">
                  <thead>
                    <tr>
                      <th>Config</th>
                      <th>Mean</th>
                      <th>Std Dev</th>
                      <th>Min</th>
                      <th>Max</th>
                      <th>Stability</th>
                    </tr>
                  </thead>
                  <tbody>
                    {robustness.map((r) => {
                      const stable =
                        r.mean !== 0 && r.std / Math.abs(r.mean) < 0.1;
                      const label = paramNames
                        .map((p) =>
                          r.params[p] !== undefined
                            ? `${p}=${r.params[p]}`
                            : "",
                        )
                        .filter(Boolean)
                        .join(", ");
                      return (
                        <tr key={r.key}>
                          <td className="mono" style={{ fontSize: ".78rem" }}>
                            {label}
                          </td>
                          <td className="mono">{r.mean.toFixed(3)}</td>
                          <td className="mono">{r.std.toFixed(3)}</td>
                          <td className="mono">{r.min.toFixed(3)}</td>
                          <td className="mono">{r.max.toFixed(3)}</td>
                          <td>
                            <Badge variant={stable ? "green" : "yellow"}>
                              {stable ? "Stable" : "Variable"}
                            </Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
