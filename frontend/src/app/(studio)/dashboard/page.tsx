"use client";

import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Topbar from "@/components/shell/Topbar";
import { useToast } from "@/components/feedback/ToastProvider";
import Modal from "@/components/feedback/Modal";
import Skeleton from "@/components/feedback/Skeleton";
import EmptyState from "@/components/feedback/EmptyState";
import StatCard from "@/components/ui/StatCard";
import Card from "@/components/ui/Card";
import Badge from "@/components/ui/Badge";
import SyntheticBadge from "@/components/SyntheticBadge";
import { simulationService } from "@/lib/services/simulationService";
import { sweepService } from "@/lib/services/sweepService";
import {
  calibrationService,
  type CalibrationCorpus,
} from "@/lib/services/calibrationService";
import { useAsync } from "@/lib/hooks/useAsync";
import { toToastMessage } from "@/lib/api/errors";
import type { SimRun, SimStatus } from "@/lib/types/simulations";
import type { SimTemplate } from "@/lib/api/adapters/templates";
import { useChainIdiom } from "@/lib/hooks/useChainIdiom";
import { useDataTheme } from "@/lib/hooks/useDataTheme";

function syntaxHighlight(json: string): string {
  return json.replace(
    /("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "num";
      if (/^"/.test(match)) cls = /:$/.test(match) ? "key" : "str";
      else if (/true|false/.test(match)) cls = "bool";
      else if (/null/.test(match)) cls = "null";
      return `<span class="${cls}">${match}</span>`;
    },
  );
}

function statusBadge(status: SimStatus) {
  if (status === "completed") return <Badge variant="green">Completed</Badge>;
  if (status === "running") return <Badge variant="yellow">Running</Badge>;
  if (status === "paused") return <Badge variant="blue">Paused</Badge>;
  if (status === "cancelled") return <Badge variant="red">Cancelled</Badge>;
  if (status === "failed") return <Badge variant="red">Failed</Badge>;
  return <Badge variant="blue">{status}</Badge>;
}

interface DashboardData {
  runs: SimRun[];
  sweepCount: number;
}

async function loadDashboardData(): Promise<DashboardData> {
  const [runs, sweeps] = await Promise.all([
    simulationService.listRuns(),
    sweepService.listSweeps(),
  ]);
  return { runs, sweepCount: sweeps.length };
}

interface VerbCardProps {
  title: string;
  description: string;
  href: string;
  icon: React.ReactNode;
}

function VerbCard({ title, description, href, icon }: VerbCardProps) {
  const router = useRouter();
  return (
    <button
      type="button"
      className="verb-card"
      onClick={() => router.push(href)}
      aria-label={`${title}: ${description}`}
    >
      <span className="verb-card-icon">
        <svg viewBox="0 0 24 24" fill="none" width="22" height="22">
          {icon}
        </svg>
      </span>
      <span className="verb-card-body">
        <strong>{title}</strong>
        <span>{description}</span>
      </span>
      <span className="verb-card-arrow" aria-hidden>
        →
      </span>
    </button>
  );
}

const VERB_REPLAY_ICON = (
  <>
    <path
      d="M5 12A7 7 0 1 1 8 18"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
    <path
      d="M3 18H8V13"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </>
);

const VERB_BUILD_ICON = (
  <>
    <path
      d="M3 8L12 3L21 8L21 17L12 21L3 17L3 8Z"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinejoin="round"
    />
    <path
      d="M3 8L12 13L21 8M12 13V21"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinejoin="round"
    />
  </>
);

export default function DashboardPage() {
  const router = useRouter();
  const { showToast } = useToast();

  const { data, loading, error, refetch } = useAsync(loadDashboardData, []);
  const templatesState = useAsync<SimTemplate[]>(
    () => simulationService.getTemplates(),
    [],
  );
  const calibrationState = useAsync<CalibrationCorpus>(
    () => calibrationService.getCorpus(),
    [],
  );
  const featuredTemplates = useMemo(
    () => (templatesState.data ?? []).filter((t) => t.featured),
    [templatesState.data],
  );

  const [detailModalOpen, setDetailModalOpen] = useState(false);
  const [specModalOpen, setSpecModalOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [specRun, setSpecRun] = useState<SimRun | undefined>(undefined);
  const [specLoading, setSpecLoading] = useState(false);

  const runs = data?.runs ?? [];
  const selectedRun = runs.find((r) => r.id === detailId);

  const dashSpec =
    runs.length > 0 &&
    runs.every((r) => {
      const model = r.spec?.execution?.model;
      return model === "solana" || model === "solana_like";
    })
      ? runs[0].spec
      : null;
  const idiom = useChainIdiom(dashSpec);
  useDataTheme(dashSpec);

  const totals = useMemo(() => {
    const activeCount = runs.filter((r) => r.status === "running").length;
    const totalRounds = runs.reduce((s, r) => s + (r.currentRound || 0), 0);
    const agentsCreated = runs.reduce((s, r) => s + (r.agents || 0), 0);
    return { activeCount, totalRounds, agentsCreated };
  }, [runs]);

  const resumeRun = useMemo<SimRun | null>(() => {
    if (runs.length === 0) return null;
    const inFlight = runs.find(
      (r) => r.status === "running" || r.status === "paused",
    );
    if (inFlight) return inFlight;
    return runs[0] ?? null;
  }, [runs]);

  const calibrationSummary = useMemo(() => {
    const corpus = calibrationState.data;
    if (!corpus) return null;
    const total = corpus.slots.length;
    const withRuns = corpus.slots.filter((s) => s.lastRun !== null).length;
    const regressing = corpus.slots
      .flatMap((s) => s.trend ?? [])
      .filter((t) => t.direction === "regressing").length;
    return { total, withRuns, regressing };
  }, [calibrationState.data]);

  const openSpecModal = useCallback(
    async (runId: string) => {
      setSpecModalOpen(true);
      setSpecLoading(true);
      try {
        const full = await simulationService.getRun(runId);
        setSpecRun(full);
      } catch (err) {
        showToast(toToastMessage(err), "error");
        setSpecModalOpen(false);
      } finally {
        setSpecLoading(false);
      }
    },
    [showToast],
  );

  const openDetailModal = (id: string) => {
    setDetailId(id);
    setDetailModalOpen(true);
  };

  const progressPct =
    resumeRun && resumeRun.totalRounds > 0
      ? Math.min(
          100,
          Math.round((resumeRun.currentRound / resumeRun.totalRounds) * 100),
        )
      : 0;

  return (
    <>
      <Topbar title="Dashboard" spec={dashSpec} />

      <div id="content" className="fade-in">
        {/* ── Resume hero ─────────────────────────────────── */}
        {resumeRun && (
          <div className="resume-hero" data-status={resumeRun.status}>
            <div className="resume-hero-body">
              <span className="resume-hero-eyebrow">Resume</span>
              <h3>
                <span className="resume-hero-icon" aria-hidden>
                  ▶
                </span>
                {resumeRun.market ? `${resumeRun.market} · ` : ""}
                {resumeRun.id}
              </h3>
              <div className="resume-hero-meta">
                {statusBadge(resumeRun.status)}
                <span className="resume-hero-meta-text">
                  {resumeRun.currentRound} / {resumeRun.totalRounds}{" "}
                  {idiom.rounds_label}
                  {resumeRun.agents ? ` · ${resumeRun.agents} agents` : ""}
                </span>
              </div>
              {resumeRun.totalRounds > 0 && (
                <div className="progress-bar">
                  <div
                    className="fill blue"
                    style={{ width: `${progressPct}%` }}
                  />
                </div>
              )}
            </div>
            <div className="resume-hero-actions">
              {resumeRun.status === "completed" ? (
                <button
                  className="btn btn-primary btn-sm cta-primary"
                  onClick={() => router.push(`/results/${resumeRun.id}`)}
                >
                  View results
                </button>
              ) : (
                <button
                  className="btn btn-primary btn-sm cta-primary"
                  onClick={() => router.push(`/runner/${resumeRun.id}`)}
                >
                  Continue
                </button>
              )}
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => openDetailModal(resumeRun.id)}
              >
                Details
              </button>
            </div>
          </div>
        )}

        {/* ── Workspace verbs ─────────────────────────────── */}
        <div className="verb-grid">
          <VerbCard
            title="Replay a slot"
            description="Counterfactual replay of a Solana mainnet slot"
            href="/replay"
            icon={VERB_REPLAY_ICON}
          />
          <VerbCard
            title="Build a scenario"
            description="Synthetic markets, agents, and sweeps"
            href="/builder"
            icon={VERB_BUILD_ICON}
          />
        </div>

        {/* ── Featured templates (PRD US-002) ─────────────── */}
        {featuredTemplates.length > 0 && (
          <Card title="Featured demos" style={{ marginBottom: 16 }}>
            <div className="grid-3">
              {featuredTemplates.map((tpl) => (
                <div
                  key={tpl.id}
                  className="card"
                  data-testid="template-card"
                  data-template-id={tpl.id}
                  data-featured="true"
                  onClick={() => router.push(`/builder?template=${tpl.id}`)}
                  style={{
                    cursor: "pointer",
                    position: "relative",
                    borderColor: "var(--accent)",
                    boxShadow: "0 0 0 1px var(--accent)",
                    margin: 0,
                  }}
                >
                  <span
                    data-testid="template-featured-ribbon"
                    style={{
                      position: "absolute",
                      top: 8,
                      right: 8,
                      background: "var(--accent)",
                      color: "var(--bg-0)",
                      fontSize: ".7rem",
                      fontWeight: 700,
                      padding: "2px 8px",
                      borderRadius: 4,
                      letterSpacing: ".03em",
                      textTransform: "uppercase",
                    }}
                  >
                    Featured demo
                  </span>
                  <h3 style={{ marginBottom: 4, fontSize: "1rem" }}>{tpl.name}</h3>
                  <p
                    style={{
                      color: "var(--text-2)",
                      fontSize: ".85rem",
                      marginBottom: 8,
                    }}
                  >
                    {tpl.description}
                  </p>
                  <div
                    style={{
                      display: "flex",
                      gap: 6,
                      flexWrap: "wrap",
                      alignItems: "center",
                    }}
                  >
                    <span className="badge badge-blue">{tpl.category}</span>
                    <SyntheticBadge template={tpl} />
                  </div>
                  {tpl.id === "solana-sandwich-lighthouse" && (
                    <Link
                      href="/help/lighthouse-scenario"
                      data-testid="template-what-this-is-link"
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        display: "inline-block",
                        marginTop: 8,
                        fontSize: ".8rem",
                        color: "var(--accent)",
                        textDecoration: "underline",
                      }}
                    >
                      What this is →
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* ── Recent simulations ──────────────────────────── */}
        <Card
          title="Recent simulations"
          actions={
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => router.push("/builder")}
            >
              + New
            </button>
          }
        >
          {loading ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 12,
                padding: "8px 0",
              }}
            >
              <Skeleton height={44} />
              <Skeleton height={44} />
              <Skeleton height={44} />
            </div>
          ) : error ? (
            <EmptyState
              title="Failed to load runs"
              description={toToastMessage(error)}
              action={
                <button className="btn btn-secondary btn-sm" onClick={refetch}>
                  Retry
                </button>
              }
            />
          ) : runs.length === 0 ? (
            <EmptyState
              title="No simulations yet"
              description="Pick a Workspace verb above to start."
              action={
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => router.push("/replay")}
                >
                  Try replay
                </button>
              }
            />
          ) : (
            runs.slice(0, 6).map((run) => (
              <div
                key={run.id}
                className="sim-row"
                data-testid="sim-row"
                data-run-id={run.id}
                onClick={() => openDetailModal(run.id)}
              >
                <span className="sim-id">{run.id}</span>
                <span className="sim-market">{run.market}</span>
                <span className="sim-agents">{run.agents} agents</span>
                <span className="sim-rounds">
                  {run.currentRound} / {run.totalRounds}
                </span>
                <span className="sim-status">{statusBadge(run.status)}</span>
                <span className="sim-actions">
                  {run.status === "completed" ? (
                    <>
                      <button
                        className="btn-icon"
                        title="View results"
                        aria-label="View results"
                        onClick={(e) => {
                          e.stopPropagation();
                          router.push(`/results/${run.id}`);
                        }}
                      >
                        <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
                          <path
                            d="M2 16V8M6 16V4M10 16V10M14 16V6"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                          />
                        </svg>
                      </button>
                      <button
                        className="btn-icon"
                        title="View JSON"
                        aria-label="View JSON"
                        onClick={(e) => {
                          e.stopPropagation();
                          openSpecModal(run.id);
                        }}
                      >
                        <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
                          <path
                            d="M6 6h6M6 9h4M6 12h5"
                            stroke="currentColor"
                            strokeWidth="1.3"
                            strokeLinecap="round"
                          />
                          <rect
                            x="2"
                            y="2"
                            width="14"
                            height="14"
                            rx="2"
                            stroke="currentColor"
                            strokeWidth="1.5"
                          />
                        </svg>
                      </button>
                    </>
                  ) : (
                    <button
                      className="btn-icon"
                      title="Open in runner"
                      aria-label="Open in runner"
                      onClick={(e) => {
                        e.stopPropagation();
                        router.push(`/runner/${run.id}`);
                      }}
                    >
                      <svg width="14" height="14" viewBox="0 0 18 18" fill="none">
                        <polygon points="4,2 16,9 4,16" fill="currentColor" />
                      </svg>
                    </button>
                  )}
                </span>
              </div>
            ))
          )}
        </Card>

        {/* ── Stats + Calibration row ─────────────────────── */}
        <div className="stats-calib-grid">
          <div className="stats-row">
            <StatCard
              label="Active sims"
              value={loading ? "…" : totals.activeCount}
            />
            <StatCard
              label={`Total ${idiom.rounds_label}`}
              value={loading ? "…" : totals.totalRounds}
            />
            <StatCard
              label="Agents created"
              value={loading ? "…" : totals.agentsCreated}
            />
            <StatCard
              label="Sweep jobs"
              value={loading ? "…" : data?.sweepCount ?? 0}
            />
          </div>
          <div className="calib-summary">
            <div className="calib-summary-head">
              <span className="calib-summary-eyebrow">Calibration health</span>
              <Link href="/calibration" className="calib-summary-link">
                View →
              </Link>
            </div>
            {calibrationState.loading ? (
              <Skeleton height={80} />
            ) : calibrationState.error || !calibrationSummary ? (
              <p className="calib-summary-empty">
                Calibration corpus unavailable.
              </p>
            ) : (
              <>
                <div className="calib-summary-stats">
                  <div>
                    <strong>{calibrationSummary.total}</strong>
                    <span>corpus slots</span>
                  </div>
                  <div>
                    <strong>{calibrationSummary.withRuns}</strong>
                    <span>with runs</span>
                  </div>
                  <div>
                    <strong
                      style={{
                        color:
                          calibrationSummary.regressing > 0
                            ? "var(--red)"
                            : "var(--green)",
                      }}
                    >
                      {calibrationSummary.regressing}
                    </strong>
                    <span>regressing</span>
                  </div>
                </div>
                <p className="calib-summary-foot">
                  Trust artifact for the Solana lab — re-run the regression suite
                  to refresh.
                </p>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Detail Modal ──────────────────────────────────── */}
      <Modal
        open={detailModalOpen}
        onClose={() => setDetailModalOpen(false)}
        title={detailId ?? ""}
        maxWidth={640}
        actions={
          <button
            className="btn btn-primary"
            onClick={() => setDetailModalOpen(false)}
          >
            Close
          </button>
        }
      >
        {selectedRun && (
          <>
            <div className="grid-2" style={{ marginBottom: 16 }}>
              <StatCard label="Market" value={selectedRun.market} valueSize="1rem" />
              <StatCard label="Status" value={selectedRun.status} valueSize="1rem" />
              <StatCard label="Agents" value={selectedRun.agents} valueSize="1rem" />
              <StatCard
                label="Progress"
                value={`${selectedRun.currentRound} / ${selectedRun.totalRounds}`}
                valueSize="1rem"
              />
            </div>
            <table>
              <tbody>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Seed</td>
                  <td className="mono">{selectedRun.seed}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Execution</td>
                  <td>{selectedRun.exec}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Ordering</td>
                  <td>{selectedRun.ordering}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Fee Model</td>
                  <td>{selectedRun.fee}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--text-2)" }}>Price Feed</td>
                  <td>{selectedRun.feed}</td>
                </tr>
              </tbody>
            </table>
            <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => {
                  setDetailModalOpen(false);
                  router.push(`/runner/${selectedRun.id}`);
                }}
              >
                Open in Runner
              </button>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setDetailModalOpen(false);
                  router.push(`/results/${selectedRun.id}`);
                }}
              >
                View Results
              </button>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  setDetailModalOpen(false);
                  openSpecModal(selectedRun.id);
                }}
              >
                View Spec
              </button>
            </div>
          </>
        )}
      </Modal>

      {/* ── Spec Modal ────────────────────────────────────── */}
      <Modal
        open={specModalOpen}
        onClose={() => {
          setSpecModalOpen(false);
          setSpecRun(undefined);
        }}
        title="RunSpec Preview"
        actions={
          <>
            <button
              className="btn btn-secondary"
              disabled={!specRun}
              onClick={() => {
                if (specRun?.spec) {
                  navigator.clipboard?.writeText(
                    JSON.stringify(specRun.spec, null, 2),
                  );
                  showToast("Spec copied to clipboard", "success");
                }
              }}
            >
              Copy JSON
            </button>
            <button
              className="btn btn-primary"
              onClick={() => {
                setSpecModalOpen(false);
                setSpecRun(undefined);
              }}
            >
              Close
            </button>
          </>
        }
      >
        {specLoading ? (
          <Skeleton height={240} />
        ) : specRun ? (
          <div
            className="json-view"
            dangerouslySetInnerHTML={{
              __html: syntaxHighlight(JSON.stringify(specRun.spec, null, 2)),
            }}
          />
        ) : (
          <EmptyState title="No spec loaded" />
        )}
      </Modal>
    </>
  );
}
