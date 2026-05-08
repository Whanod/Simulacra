"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
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
} from "@/lib/services/calibrationService";
import {
  replayService,
  type ReplayResult,
} from "@/lib/services/replayService";

type ScoreboardStatus = "within" | "breached" | "recorded" | "not_measured";

interface ScoreboardRow {
  metric: string;
  latestError: string;
  threshold: string;
  status: ScoreboardStatus;
  statusLabel: string;
}

const numberFormat = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 6,
});

function parseSlotParam(value: string | string[] | undefined): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) return null;
  const slot = Number(raw);
  return Number.isSafeInteger(slot) ? slot : null;
}

function formatNumber(value: number): string {
  return numberFormat.format(value);
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`;
}

function formatThreshold(t: CalibrationThreshold | undefined): string {
  if (!t) return "No threshold";
  if (t.thresholdRelative !== null) {
    return `+/- ${formatPercent(t.thresholdRelative)} rel.`;
  }
  if (t.thresholdAbsolute !== null) {
    return `+/- ${formatNumber(t.thresholdAbsolute)} abs.`;
  }
  return "No threshold";
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "No completed benchmark run";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function relativeError(absError: number, actual: number | null): number | null {
  if (actual === null || actual === 0 || !Number.isFinite(actual)) return null;
  return absError / Math.abs(actual);
}

function expectedMetricNames(expected: Record<string, unknown>): string[] {
  return Object.keys(expected).filter((key) => key !== "decoded_coverage");
}

function buildScoreboardRows(
  slot: CalibrationCorpusSlot,
  thresholds: CalibrationThreshold[],
): ScoreboardRow[] {
  const thresholdByMetric = new Map(thresholds.map((t) => [t.metric, t]));
  const latest = slot.lastRun?.perMetricError ?? {};
  const metrics = new Set<string>([
    ...thresholds.map((t) => t.metric),
    ...Object.keys(latest),
    ...expectedMetricNames(slot.expected),
  ]);

  return Array.from(metrics)
    .sort()
    .map((metric) => {
      const band = latest[metric];
      const threshold = thresholdByMetric.get(metric);
      if (!band) {
        return {
          metric,
          latestError: "Not measured by the latest replay artifact",
          threshold: formatThreshold(threshold),
          status: "not_measured",
          statusLabel: "not measured",
        };
      }

      const rel = relativeError(band.absError, band.actual);
      let status: ScoreboardStatus = "recorded";
      if (threshold?.thresholdAbsolute !== null && threshold?.thresholdAbsolute !== undefined) {
        status = band.absError <= threshold.thresholdAbsolute ? "within" : "breached";
      } else if (
        threshold?.thresholdRelative !== null &&
        threshold?.thresholdRelative !== undefined &&
        rel !== null
      ) {
        status = rel <= threshold.thresholdRelative ? "within" : "breached";
      }

      const detail =
        rel === null
          ? `abs ${formatNumber(band.absError)}`
          : `abs ${formatNumber(band.absError)} (${formatPercent(rel)} rel.)`;
      return {
        metric,
        latestError: detail,
        threshold: formatThreshold(threshold),
        status,
        statusLabel:
          status === "within"
            ? "within threshold"
            : status === "breached"
              ? "over threshold"
              : "recorded",
      };
    });
}

function statusVariant(status: ScoreboardStatus): "green" | "yellow" | "red" | "blue" {
  if (status === "within") return "green";
  if (status === "breached") return "red";
  if (status === "recorded") return "blue";
  return "yellow";
}

function SlotSummary({ slot }: { slot: CalibrationCorpusSlot }) {
  return (
    <Card
      title={`Benchmark slot ${slot.slot}`}
      badge={<Badge variant={slot.lastRun ? "green" : "yellow"}>{slot.lastRun ? "replayed" : "pending"}</Badge>}
    >
      <div className="grid-3">
        <div>
          <div className="hint">Programs</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
            {slot.programs.length === 0 ? (
              <span className="hint">No programs declared</span>
            ) : (
              slot.programs.map((program) => (
                <Badge key={program} variant="blue">
                  {program.slice(0, 8)}...
                </Badge>
              ))
            )}
          </div>
        </div>
        <div>
          <div className="hint">Latest run</div>
          <div data-testid="benchmark-last-run" style={{ marginTop: 6 }}>
            {formatTimestamp(slot.lastRun?.createdAt ?? null)}
          </div>
        </div>
        <div>
          <div className="hint">Recorded replays</div>
          <div data-testid="benchmark-run-count" style={{ marginTop: 6 }}>
            {slot.runCount}
          </div>
        </div>
      </div>
    </Card>
  );
}

function ScoreboardTable({ rows }: { rows: ScoreboardRow[] }) {
  return (
    <Card title="Scoreboard">
      <table data-testid="benchmark-scoreboard">
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>Metric</th>
            <th style={{ textAlign: "left" }}>Latest error vs. mainnet</th>
            <th style={{ textAlign: "left" }}>Threshold</th>
            <th style={{ textAlign: "left" }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.metric}
              data-testid="benchmark-scoreboard-row"
              data-metric={row.metric}
              data-status={row.status}
            >
              <td className="mono">{row.metric}</td>
              <td>{row.latestError}</td>
              <td>{row.threshold}</td>
              <td>
                <Badge variant={statusVariant(row.status)}>{row.statusLabel}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

export default function BenchmarkSlotPage() {
  const params = useParams<{ slot?: string }>();
  const slotNumber = parseSlotParam(params.slot);
  const corpusState = useAsync<CalibrationCorpus>(
    () => calibrationService.getCorpus(),
    [slotNumber],
  );
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(undefined);
  const [runResult, setRunResult] = useState<ReplayResult | null>(null);

  const selectedSlot = useMemo(() => {
    if (slotNumber === null || !corpusState.data) return null;
    return corpusState.data.slots.find((slot) => slot.slot === slotNumber) ?? null;
  }, [corpusState.data, slotNumber]);

  const scoreboardRows = useMemo(() => {
    if (!selectedSlot || !corpusState.data) return [];
    return buildScoreboardRows(selectedSlot, corpusState.data.thresholds);
  }, [corpusState.data, selectedSlot]);

  async function runBenchmark() {
    if (!selectedSlot) return;
    setRunning(true);
    setRunError(undefined);
    try {
      const result = await replayService.submitReplay({
        slotStart: selectedSlot.slot,
        slotEnd: selectedSlot.slot,
        counterfactuals: [],
      });
      setRunResult(result);
      corpusState.refetch();
    } catch (err) {
      setRunError(err);
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <Topbar title="Benchmark" />
      <div id="content" className="fade-in" data-testid="benchmark-page">
        {slotNumber === null && (
          <EmptyState
            title="Invalid benchmark slot"
            description="Open a benchmark with a numeric slot path."
            action={
              <Link className="btn btn-secondary btn-sm" href="/calibration">
                Back to calibration
              </Link>
            }
          />
        )}

        {slotNumber !== null && corpusState.loading && (
          <div className="grid-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="card">
                <Skeleton height={18} width="60%" />
                <div style={{ marginTop: 8 }}>
                  <Skeleton height={12} />
                </div>
              </div>
            ))}
          </div>
        )}

        {slotNumber !== null && !corpusState.loading && corpusState.error != null && (
          <EmptyState
            title="Failed to load benchmark dataset"
            description={toToastMessage(corpusState.error)}
            action={
              <button className="btn btn-secondary btn-sm" onClick={corpusState.refetch}>
                Retry
              </button>
            }
          />
        )}

        {slotNumber !== null && !corpusState.loading && !corpusState.error && !selectedSlot && (
          <EmptyState
            title={`Slot ${slotNumber} is not in the benchmark dataset`}
            description="Committed corpus slots are listed on the calibration dashboard."
            action={
              <Link className="btn btn-secondary btn-sm" href="/calibration">
                View corpus
              </Link>
            }
          />
        )}

        {selectedSlot && corpusState.data && (
          <div style={{ display: "grid", gap: 20 }}>
            <div
              className="card"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 16,
                flexWrap: "wrap",
              }}
            >
              <div>
                <div className="hint">Benchmark dataset</div>
                <h3 data-testid="benchmark-slot" style={{ margin: "4px 0 0" }}>
                  Slot {selectedSlot.slot}
                </h3>
                <p className="hint" style={{ margin: "6px 0 0" }}>
                  Re-executes this corpus slot against the current replay engine and refreshes the scoreboard from durable run artifacts.
                </p>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Link className="btn btn-secondary btn-sm" href="/calibration">
                  Corpus
                </Link>
                <button
                  className="btn btn-primary btn-sm"
                  data-testid="benchmark-run-button"
                  disabled={running}
                  onClick={runBenchmark}
                >
                  {running ? "Running..." : "Run this benchmark"}
                </button>
              </div>
            </div>

            {runError != null && (
              <div className="bundle-error" data-testid="benchmark-run-error">
                {toToastMessage(runError)}
              </div>
            )}

            {runResult && (
              <Card
                title="Submitted benchmark replay"
                badge={<Badge variant={runResult.eligibleForCalibration ? "green" : "yellow"}>{runResult.replayKind}</Badge>}
                actions={
                  <Link className="btn btn-secondary btn-sm" href={`/results/${runResult.runId}`}>
                    Open result
                  </Link>
                }
              >
                <div className="grid-3">
                  <div>
                    <div className="hint">Run ID</div>
                    <div className="mono" data-testid="benchmark-run-id">
                      {runResult.runId}
                    </div>
                  </div>
                  <div>
                    <div className="hint">Slots loaded</div>
                    <div>{runResult.slotsLoaded}</div>
                  </div>
                  <div>
                    <div className="hint">Decoded share</div>
                    <div>{formatPercent(runResult.decodedTransactionShare)}</div>
                  </div>
                </div>
              </Card>
            )}

            <SlotSummary slot={selectedSlot} />
            <ScoreboardTable rows={scoreboardRows} />

            <Card title="Manifest expected values">
              <pre className="replay-json-block" data-testid="benchmark-manifest-expected">
                {JSON.stringify(selectedSlot.expected, null, 2)}
              </pre>
            </Card>
          </div>
        )}
      </div>
    </>
  );
}
