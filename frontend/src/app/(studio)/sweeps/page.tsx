"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { sweepService } from "@/lib/services/sweepService";
import { useAsync } from "@/lib/hooks/useAsync";
import { ApiError } from "@/lib/api/errors";
import type { RunSpec, SweepConfig, SweepRun } from "@/lib/types";

const DEFAULT_BASE_SPEC: RunSpec = {
  market: {
    type: "cfamm",
    num_assets: 2,
    initial_liquidity: 1_000_000,
    token_decimals: 9,
  },
  clock: { type: "block", block_time: 1, epoch_length: 1 },
  execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
  fee_model: { type: "flat", rate_bps: 30 },
  agents: {
    total: 4,
    mix: {
      noise: 1,
      informed: 0,
      arbitrageur: 0,
      manipulator: 0,
      passive_lp: 0,
      rebalancing_lp: 0,
    },
    default_collateral: 1_000_000_000,
    role_params: {},
  },
  feeds: [
    {
      type: "stochastic",
      process: "gbm",
      drift: 0.0001,
      volatility: 0.02,
      initial_price: 1.0,
    },
  ],
  config: {
    num_rounds: 5,
    snapshot_interval: 1,
    seed: 42,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

interface ParamRow {
  parameter: string;
  min: string;
  max: string;
  steps: string;
}

const INITIAL_PARAMS: ParamRow[] = [
  { parameter: "num_rounds", min: "2", max: "4", steps: "3" },
  { parameter: "snapshot_interval", min: "1", max: "2", steps: "2" },
];

function parseSeeds(raw: string): number[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => Number(s))
    .filter((n) => Number.isFinite(n));
}

function parseSweepParams(rows: ParamRow[]): {
  params: SweepConfig["params"];
  error?: string;
} {
  const parsed: SweepConfig["params"] = [];
  for (const [index, row] of rows.entries()) {
    const label = row.parameter.trim() || `parameter ${index + 1}`;
    if (row.parameter.trim().length === 0) {
      return { params: [], error: `Parameter ${index + 1} is missing a dot-path.` };
    }
    if (row.min.trim().length === 0) {
      return { params: [], error: `${label}: Min is required.` };
    }
    const min = Number(row.min);
    if (!Number.isFinite(min)) {
      return { params: [], error: `${label}: Min must be a number.` };
    }
    if (row.max.trim().length === 0) {
      return { params: [], error: `${label}: Max is required.` };
    }
    const max = Number(row.max);
    if (!Number.isFinite(max)) {
      return { params: [], error: `${label}: Max must be a number.` };
    }
    if (row.steps.trim().length === 0) {
      return { params: [], error: `${label}: Steps is required.` };
    }
    const steps = Number.parseInt(row.steps, 10);
    if (!Number.isInteger(steps) || steps < 1) {
      return { params: [], error: `${label}: Steps must be a whole number greater than 0.` };
    }
    parsed.push({
      parameter: row.parameter.trim(),
      min,
      max,
      steps,
    });
  }
  return { params: parsed };
}

export default function SweepsPage() {
  const router = useRouter();
  const { showToast } = useToast();

  const [params, setParams] = useState<ParamRow[]>(INITIAL_PARAMS);
  const [seedsRaw, setSeedsRaw] = useState("1, 2");
  const [targetMetric, setTargetMetric] = useState("rounds");
  const [direction, setDirection] = useState<"lower" | "higher">("higher");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const sweepsState = useAsync<SweepRun[]>(() => sweepService.listSweeps(), []);
  const parsedParams = useMemo(() => parseSweepParams(params), [params]);

  const totalRuns = useMemo(() => {
    const seeds = parseSeeds(seedsRaw).length;
    if (seeds === 0 || parsedParams.error) return 0;
    const combos = parsedParams.params.reduce((acc, p) => acc * p.steps, 1);
    return seeds * combos;
  }, [parsedParams, seedsRaw]);

  const updateParam = useCallback((i: number, patch: Partial<ParamRow>) => {
    setParams((prev) => {
      const next = [...prev];
      next[i] = { ...next[i], ...patch };
      return next;
    });
  }, []);

  const addParam = useCallback(() => {
    setParams((prev) => [
      ...prev,
      { parameter: "seed", min: "1", max: "3", steps: "3" },
    ]);
  }, []);

  const removeParam = useCallback((i: number) => {
    setParams((prev) => prev.filter((_, idx) => idx !== i));
  }, []);

  const handleRun = useCallback(async () => {
    if (isSubmitting) return;
    if (params.length === 0) {
      showToast("Add at least one parameter", "error");
      return;
    }
    if (parsedParams.error) {
      showToast(parsedParams.error, "error");
      return;
    }
    const seeds = parseSeeds(seedsRaw);
    if (seeds.length === 0) {
      showToast("Provide at least one seed", "error");
      return;
    }
    setIsSubmitting(true);
    try {
      const config: SweepConfig = {
        id: "",
        params: parsedParams.params,
        seeds,
        parallelWorkers: 1,
        targetMetric,
        direction,
        validityGates: [],
        totalRuns,
      };
      showToast("Running sweep…", "info");
      const result = await sweepService.createSweep(DEFAULT_BASE_SPEC, config);
      showToast("Sweep complete", "success");
      router.push(`/sweeps/${result.sweepId}`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Sweep failed";
      showToast(`Sweep failed: ${msg}`, "error");
    } finally {
      setIsSubmitting(false);
    }
  }, [
    isSubmitting,
    params,
    parsedParams,
    seedsRaw,
    targetMetric,
    direction,
    totalRuns,
    showToast,
    router,
  ]);

  const sweeps = sweepsState.data ?? [];

  return (
    <>
      <Topbar title="Parameter Sweeps" />
      <div id="content" className="fade-in">
        <div className="grid-2">
          {/* Left: Configuration */}
          <div>
            <Card title="New Sweep">
              <div className="form-section">
                <h4>Parameter Grid</h4>
                {params.map((p, i) => (
                  <div
                    key={i}
                    className="form-row"
                    style={{ marginBottom: 8 }}
                  >
                    <div className="form-group" style={{ flex: 2 }}>
                      <label>Parameter (dot-path)</label>
                      <input
                        type="text"
                        value={p.parameter}
                        onChange={(e) =>
                          updateParam(i, { parameter: e.target.value })
                        }
                      />
                    </div>
                    <div className="form-group">
                      <label>Min</label>
                      <input
                        type="number"
                        value={p.min}
                        onChange={(e) =>
                          updateParam(i, {
                            min: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="form-group">
                      <label>Max</label>
                      <input
                        type="number"
                        value={p.max}
                        onChange={(e) =>
                          updateParam(i, {
                            max: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="form-group">
                      <label>Steps</label>
                      <input
                        type="number"
                        value={p.steps}
                        min={1}
                        onChange={(e) =>
                          updateParam(i, {
                            steps: e.target.value,
                          })
                        }
                      />
                    </div>
                    {params.length > 1 && (
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ alignSelf: "flex-end" }}
                        onClick={() => removeParam(i)}
                        aria-label={`Remove parameter ${i + 1}`}
                      >
                        ×
                      </button>
                    )}
                  </div>
                ))}
                <button className="btn btn-secondary btn-sm" onClick={addParam}>
                  + Add Parameter
                </button>
              </div>

              <div className="form-section">
                <h4>Seeds</h4>
                <div className="form-group">
                  <label>Seeds (comma-separated)</label>
                  <input
                    type="text"
                    value={seedsRaw}
                    onChange={(e) => setSeedsRaw(e.target.value)}
                    placeholder="1, 2, 3"
                  />
                </div>
                {parsedParams.error ? (
                  <p className="hint" style={{ color: "var(--red)" }}>
                    {parsedParams.error}
                  </p>
                ) : (
                  <p className="hint">
                    Total runs: <strong>{totalRuns} simulations</strong>
                  </p>
                )}
              </div>

              <div className="form-section">
                <h4>Target Metric</h4>
                <div className="form-row">
                  <div className="form-group">
                    <label>Metric name</label>
                    <input
                      type="text"
                      value={targetMetric}
                      onChange={(e) => setTargetMetric(e.target.value)}
                    />
                  </div>
                  <div className="form-group">
                    <label>Direction</label>
                    <select
                      value={direction}
                      onChange={(e) =>
                        setDirection(e.target.value as "lower" | "higher")
                      }
                    >
                      <option value="higher">Higher is better</option>
                      <option value="lower">Lower is better</option>
                    </select>
                  </div>
                </div>
                <p className="hint">
                  The backend resolves the metric from{" "}
                  <code>num_rounds_executed</code> — other metrics need backend
                  configuration.
                </p>
              </div>

              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn btn-primary"
                  style={{ flex: 1 }}
                  onClick={handleRun}
                  disabled={isSubmitting}
                  data-testid="sweep-run"
                >
                  {isSubmitting ? "Running…" : "Run Sweep"}
                </button>
              </div>
            </Card>
          </div>

          {/* Right: Existing sweeps */}
          <div>
            <Card
              title="Existing Sweeps"
              badge={
                sweepsState.data ? (
                  <Badge variant="blue">{sweeps.length} total</Badge>
                ) : null
              }
            >
              {sweepsState.loading && (
                <div>
                  <Skeleton height={24} />
                  <div style={{ marginTop: 6 }}>
                    <Skeleton height={24} />
                  </div>
                  <div style={{ marginTop: 6 }}>
                    <Skeleton height={24} />
                  </div>
                </div>
              )}
              {!sweepsState.loading && sweepsState.error != null && (
                <div>
                  <p style={{ color: "var(--red)", fontSize: ".85rem" }}>
                    Failed to load sweeps:{" "}
                    {sweepsState.error instanceof Error
                      ? sweepsState.error.message
                      : "unknown error"}
                  </p>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={sweepsState.refetch}
                    style={{ marginTop: 8 }}
                  >
                    Retry
                  </button>
                </div>
              )}
              {!sweepsState.loading &&
                sweepsState.error == null &&
                sweeps.length === 0 && (
                  <p
                    style={{ color: "var(--text-2)", fontSize: ".85rem" }}
                    data-testid="sweeps-empty"
                  >
                    No sweeps yet. Configure a grid on the left and hit “Run
                    Sweep”.
                  </p>
                )}
              {!sweepsState.loading && sweeps.length > 0 && (
                <div
                  className="table-wrap"
                  style={{ maxHeight: 400, overflowY: "auto" }}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>Sweep</th>
                        <th>Status</th>
                        <th>Params</th>
                        <th>Runs</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sweeps.map((s) => (
                        <tr
                          key={s.id}
                          data-testid="sweep-row"
                          data-sweep-id={s.id}
                          style={{ cursor: "pointer" }}
                          onClick={() => router.push(`/sweeps/${s.id}`)}
                        >
                          <td className="mono">{s.id.slice(0, 10)}</td>
                          <td>
                            <Badge
                              variant={
                                s.status === "completed"
                                  ? "green"
                                  : s.status === "running"
                                    ? "yellow"
                                    : "blue"
                              }
                            >
                              {s.status}
                            </Badge>
                          </td>
                          <td>
                            {s.config.params
                              .map((p) => p.parameter)
                              .join(" × ") || "—"}
                          </td>
                          <td className="mono">{s.totalRuns}</td>
                          <td>
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                router.push(`/sweeps/${s.id}`);
                              }}
                            >
                              Open
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
