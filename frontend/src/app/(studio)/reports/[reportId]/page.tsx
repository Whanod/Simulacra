"use client";

import { useCallback, useEffect, useMemo, useRef, useState, use } from "react";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import Skeleton from "@/components/feedback/Skeleton";
import { ChartCanvas } from "@/components/charts";
import { reportService } from "@/lib/services/reportService";
import { simulationService } from "@/lib/services/simulationService";
import { ApiError } from "@/lib/api/errors";
import type { ChartData } from "@/lib/api/adapters/runs";
import type { Report, ReportSection, SimMetrics } from "@/lib/types";

type SectionType = ReportSection["type"];

const ADDABLE_TYPES: SectionType[] = [
  "summary",
  "chart",
  "metrics",
  "notes",
];

const SECTION_TITLES: Record<SectionType, string> = {
  summary: "Summary",
  chart: "Chart",
  metrics: "Metrics",
  agents: "Agents",
  notes: "Notes",
  export: "Export",
};

function newSectionId() {
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

function fmtMetric(value: number | null | undefined, digits: number): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}

function ChartSection({
  runId,
  availableRunIds,
  onRunChange,
}: {
  runId: string | undefined;
  availableRunIds: string[];
  onRunChange: (runId: string) => void;
}) {
  const [data, setData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    simulationService
      .getResultCharts(runId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <div>
      <div className="form-group" style={{ marginBottom: 8 }}>
        <label>Source run</label>
        <select
          value={runId ?? ""}
          onChange={(e) => onRunChange(e.target.value)}
        >
          <option value="">—</option>
          {availableRunIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      {!runId && (
        <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
          Pick a run to render its price chart.
        </p>
      )}
      {loading && <Skeleton height={180} />}
      {error && (
        <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{error}</p>
      )}
      {!loading && data && data.priceData.length > 0 && (
        <ChartCanvas
          height={200}
          datasets={data.priceData.slice(0, 2).map((row, i) => ({
            data: row,
            color: i === 0 ? "#6c8aff" : "#34d399",
            label: data.priceLabels[i] ?? `TKN-${i}`,
            fill: i === 0,
          }))}
        />
      )}
      {!loading && data && data.priceData.length === 0 && (
        <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
          Run has no price history.
        </p>
      )}
    </div>
  );
}

function MetricsSection({
  runId,
  availableRunIds,
  onRunChange,
}: {
  runId: string | undefined;
  availableRunIds: string[];
  onRunChange: (runId: string) => void;
}) {
  const [metrics, setMetrics] = useState<SimMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    simulationService
      .getMetrics(runId)
      .then((m) => {
        if (!cancelled) setMetrics(m);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <div>
      <div className="form-group" style={{ marginBottom: 8 }}>
        <label>Source run</label>
        <select
          value={runId ?? ""}
          onChange={(e) => onRunChange(e.target.value)}
        >
          <option value="">—</option>
          {availableRunIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      {!runId && (
        <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
          Pick a run to show its metrics.
        </p>
      )}
      {loading && <Skeleton height={80} />}
      {error && (
        <p style={{ color: "var(--red)", fontSize: ".82rem" }}>{error}</p>
      )}
      {!loading && metrics && (
        <div className="grid-4">
          <div className="stat-card">
            <span className="label">KL Divergence</span>
            <span
              className="value"
              style={{ color: "var(--green)", fontSize: "1.1rem" }}
            >
              {fmtMetric(metrics.klDivergence, 3)}
            </span>
          </div>
          <div className="stat-card">
            <span className="label">Convergence</span>
            <span className="value" style={{ fontSize: "1.1rem" }}>
              {fmtMetric(metrics.convergenceSpeed, 2)}
            </span>
          </div>
          <div className="stat-card">
            <span className="label">LP Profit</span>
            <span
              className="value"
              style={{ color: "var(--green)", fontSize: "1.1rem" }}
            >
              {metrics.lpProfitability !== null
                ? metrics.lpProfitability.toFixed(3)
                : "—"}
            </span>
          </div>
          <div className="stat-card">
            <span className="label">Composite</span>
            <span
              className="value"
              style={{ color: "var(--green)", fontSize: "1.1rem" }}
            >
              {metrics.compositeScore.toFixed(0)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ReportPage({
  params,
}: {
  params: Promise<{ reportId: string }>;
}) {
  const { reportId } = use(params);
  const { showToast } = useToast();

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [availableRunIds, setAvailableRunIds] = useState<string[]>([]);

  const [title, setTitle] = useState("");
  const [sections, setSections] = useState<ReportSection[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  const hasLoadedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    hasLoadedRef.current = false;
    setLoading(true);
    setLoadError(null);
    reportService
      .getReport(reportId)
      .then((r) => {
        if (cancelled) return;
        if (!r) {
          setLoadError("Report not found");
          return;
        }
        setReport(r);
        setTitle(r.title);
        setSections(r.sections);
        hasLoadedRef.current = true;
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Failed to load report",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  // Fetch available runs for section dropdowns (chart/metrics).
  useEffect(() => {
    let cancelled = false;
    simulationService
      .listRuns()
      .then((runs) => {
        if (!cancelled) setAvailableRunIds(runs.map((r) => r.id));
      })
      .catch(() => {
        if (!cancelled) setAvailableRunIds([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced auto-save on title/sections changes.
  useEffect(() => {
    if (!hasLoadedRef.current || !report) return;
    if (report.status === "published") return;
    const timeout = window.setTimeout(() => {
      setIsSaving(true);
      reportService
        .updateReport(reportId, { title, sections })
        .then((updated) => {
          setReport(updated);
        })
        .catch((err) => {
          showToast(
            `Auto-save failed: ${err instanceof Error ? err.message : "unknown"}`,
            "error",
          );
        })
        .finally(() => setIsSaving(false));
    }, 400);
    return () => window.clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, sections, reportId]);

  const addSection = useCallback(
    (type: SectionType) => {
      setSections((prev) => [
        ...prev,
        {
          id: newSectionId(),
          type,
          title: SECTION_TITLES[type],
          content: "",
        },
      ]);
    },
    [],
  );

  const removeSection = useCallback((id: string) => {
    setSections((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const updateSection = useCallback(
    (id: string, patch: Partial<ReportSection>) => {
      setSections((prev) =>
        prev.map((s) => (s.id === id ? { ...s, ...patch } : s)),
      );
    },
    [],
  );

  const handlePublish = useCallback(async () => {
    if (!report) return;
    try {
      const updated = await reportService.updateReport(reportId, {
        title,
        sections,
        status: "published",
      });
      setReport(updated);
      showToast("Report published", "success");
    } catch (err) {
      showToast(
        `Publish failed: ${err instanceof Error ? err.message : "unknown"}`,
        "error",
      );
    }
  }, [report, reportId, title, sections, showToast]);

  const handleDownload = useCallback(async () => {
    if (isDownloading) return;
    setIsDownloading(true);
    try {
      const blob = await reportService.downloadBundle(reportId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${reportId}-bundle.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("Bundle downloaded", "success");
    } catch (err) {
      showToast(
        `Download failed: ${err instanceof Error ? err.message : "unknown"}`,
        "error",
      );
    } finally {
      setIsDownloading(false);
    }
  }, [reportId, isDownloading, showToast]);

  const runIdsForDropdowns = useMemo(() => {
    const bound = report?.runIds ?? [];
    const extras = availableRunIds.filter((id) => !bound.includes(id));
    return [...bound, ...extras];
  }, [report?.runIds, availableRunIds]);

  if (loading) {
    return (
      <>
        <Topbar title="Loading report…" />
        <div id="content" className="fade-in">
          <Skeleton height={28} width="50%" />
          <div style={{ marginTop: 16 }}>
            <Skeleton height={140} />
          </div>
        </div>
      </>
    );
  }

  if (loadError != null || !report) {
    return (
      <>
        <Topbar title="Report Not Found" />
        <div
          id="content"
          className="fade-in"
          style={{ textAlign: "center", padding: "60px 0" }}
        >
          <p style={{ color: "var(--text-2)", marginBottom: 16 }}>
            {loadError ?? "Unknown error"}
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar title="Report Builder" />
      <div id="content" className="fade-in">
        {/* Report Header */}
        <Card>
          <div
            style={{
              display: "flex",
              gap: 12,
              alignItems: "center",
              marginBottom: 16,
            }}
          >
            <div className="form-group" style={{ flex: 1, margin: 0 }}>
              <label>Report Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                disabled={report.status === "published"}
                data-testid="report-title"
                style={{ fontSize: "1rem", fontWeight: 600 }}
              />
            </div>
            <Badge
              variant={report.status === "published" ? "green" : "yellow"}
            >
              <span data-testid="report-status">{report.status}</span>
            </Badge>
            {isSaving && (
              <span
                style={{ color: "var(--text-2)", fontSize: ".78rem" }}
                data-testid="report-saving"
              >
                Saving…
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="btn btn-primary"
              onClick={handlePublish}
              disabled={report.status === "published"}
              data-testid="report-publish"
            >
              Publish
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleDownload}
              disabled={isDownloading}
              data-testid="report-download"
            >
              {isDownloading ? "Downloading…" : "Download bundle"}
            </button>
          </div>
        </Card>

        {/* Sections */}
        {sections.map((section) => (
          <Card key={section.id}>
            <div
              data-testid="report-section"
              data-section-type={section.type}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 12,
              }}
            >
              <input
                type="text"
                value={section.title}
                onChange={(e) =>
                  updateSection(section.id, { title: e.target.value })
                }
                disabled={report.status === "published"}
                style={{
                  background: "transparent",
                  border: "none",
                  fontSize: ".95rem",
                  fontWeight: 600,
                  color: "var(--text-0)",
                  padding: 0,
                }}
              />
              <div
                style={{ display: "flex", gap: 6, alignItems: "center" }}
              >
                <Badge variant="blue">{section.type}</Badge>
                {report.status !== "published" && (
                  <button
                    className="btn-icon"
                    onClick={() => removeSection(section.id)}
                    title="Remove section"
                  >
                    <svg width="12" height="12" viewBox="0 0 18 18" fill="none">
                      <path
                        d="M4 4L14 14M14 4L4 14"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                )}
              </div>
            </div>

            {(section.type === "summary" || section.type === "notes") && (
              <textarea
                rows={section.type === "summary" ? 4 : 3}
                style={{ width: "100%", resize: "vertical" }}
                placeholder={
                  section.type === "summary"
                    ? "Write a summary..."
                    : "Add notes..."
                }
                value={section.content ?? ""}
                disabled={report.status === "published"}
                onChange={(e) =>
                  updateSection(section.id, { content: e.target.value })
                }
              />
            )}

            {section.type === "chart" && (
              <ChartSection
                runId={section.runId}
                availableRunIds={runIdsForDropdowns}
                onRunChange={(runId) => updateSection(section.id, { runId })}
              />
            )}

            {section.type === "metrics" && (
              <MetricsSection
                runId={section.runId}
                availableRunIds={runIdsForDropdowns}
                onRunChange={(runId) => updateSection(section.id, { runId })}
              />
            )}

            {(section.type === "agents" || section.type === "export") && (
              <p style={{ color: "var(--text-2)", fontSize: ".82rem" }}>
                {section.type} section (preview not yet implemented).
              </p>
            )}
          </Card>
        ))}

        {/* Add Section */}
        {report.status !== "published" && (
          <Card>
            <div
              style={{
                display: "flex",
                gap: 8,
                justifyContent: "center",
                flexWrap: "wrap",
              }}
            >
              {ADDABLE_TYPES.map((type) => (
                <button
                  key={type}
                  className="btn btn-secondary btn-sm"
                  data-testid={`add-section-${type}`}
                  onClick={() => addSection(type)}
                >
                  + {SECTION_TITLES[type]}
                </button>
              ))}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
