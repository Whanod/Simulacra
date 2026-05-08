import type { Report, ReportSection } from "@/lib/types/reports";

// ── Backend shapes ─────────────────────────────────────────────────────────

export interface ApiReportManifest {
  title?: string;
  description?: string | null;
  run_ids?: string[];
  sweep_ids?: string[];
  charts?: unknown[];
  exports?: unknown[];
  raw_artifacts?: string[];
  sections?: unknown;
  [key: string]: unknown;
}

export interface ApiReportRow {
  report_id: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  has_bundle?: boolean;
  manifest?: ApiReportManifest | null;
  [key: string]: unknown;
}

export interface ApiReportsListResponse {
  reports: ApiReportRow[];
  count?: number;
  limit?: number;
  offset?: number;
}

export interface ApiReportDetailResponse {
  report: {
    report_id: string;
    status?: string;
    created_at?: string;
    updated_at?: string;
    has_bundle?: boolean;
  };
  manifest?: ApiReportManifest | null;
}

export interface ApiReportCreateResponse {
  report_id: string;
  manifest: ApiReportManifest;
}

// ── Section type parsing ───────────────────────────────────────────────────

const VALID_SECTION_TYPES: ReadonlyArray<ReportSection["type"]> = [
  "summary",
  "chart",
  "metrics",
  "agents",
  "notes",
  "export",
];

function parseSection(raw: unknown, fallbackId: string): ReportSection | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const type = typeof obj.type === "string" ? obj.type : "";
  if (!VALID_SECTION_TYPES.includes(type as ReportSection["type"])) return null;
  return {
    id: typeof obj.id === "string" ? obj.id : fallbackId,
    type: type as ReportSection["type"],
    title: typeof obj.title === "string" ? obj.title : "",
    content: typeof obj.content === "string" ? obj.content : undefined,
    runId: typeof obj.runId === "string" ? obj.runId : undefined,
    sweepId: typeof obj.sweepId === "string" ? obj.sweepId : undefined,
  };
}

export function manifestToSections(manifest: ApiReportManifest | null | undefined): ReportSection[] {
  if (!manifest) return [];
  const raw = manifest.sections;
  if (!Array.isArray(raw)) return [];
  const out: ReportSection[] = [];
  raw.forEach((item, i) => {
    const parsed = parseSection(item, `s-${i}`);
    if (parsed) out.push(parsed);
  });
  return out;
}

export function sectionsToManifest(sections: ReportSection[]): Record<string, unknown>[] {
  return sections.map((section) => ({
    id: section.id,
    type: section.type,
    title: section.title,
    ...(section.content !== undefined ? { content: section.content } : {}),
    ...(section.runId !== undefined ? { runId: section.runId } : {}),
    ...(section.sweepId !== undefined ? { sweepId: section.sweepId } : {}),
  }));
}

// ── Status mapping ─────────────────────────────────────────────────────────

function mapReportStatus(raw: string | undefined): Report["status"] {
  if (!raw) return "draft";
  const s = raw.toLowerCase();
  if (s === "published") return "published";
  return "draft"; // "ready" and "draft" → draft (frontend only tracks two states)
}

// ── Report mapping ─────────────────────────────────────────────────────────

function isDetailResponse(
  raw: ApiReportRow | ApiReportDetailResponse,
): raw is ApiReportDetailResponse {
  return typeof (raw as ApiReportDetailResponse).report === "object";
}

export function fromApiReport(raw: ApiReportRow | ApiReportDetailResponse): Report {
  const row: ApiReportDetailResponse["report"] | ApiReportRow = isDetailResponse(raw)
    ? raw.report
    : raw;
  const manifest: ApiReportManifest | null | undefined = isDetailResponse(raw)
    ? raw.manifest
    : (raw as ApiReportRow).manifest;

  return {
    id: row.report_id,
    title: typeof manifest?.title === "string" ? manifest.title : "Untitled report",
    createdAt: row.created_at ?? new Date().toISOString(),
    updatedAt: row.updated_at ?? row.created_at ?? new Date().toISOString(),
    sections: manifestToSections(manifest),
    runIds: Array.isArray(manifest?.run_ids)
      ? manifest.run_ids.filter((x): x is string => typeof x === "string")
      : [],
    sweepIds: Array.isArray(manifest?.sweep_ids)
      ? manifest.sweep_ids.filter((x): x is string => typeof x === "string")
      : [],
    status: mapReportStatus(row.status),
  };
}

export function fromApiReports(raws: ApiReportRow[]): Report[] {
  return raws.map((r) => fromApiReport(r));
}

export function reportToApi(
  report: Pick<
    Report,
    "title" | "sections" | "runIds" | "sweepIds" | "status"
  >,
): Record<string, unknown> {
  return {
    title: report.title,
    run_ids: report.runIds,
    sweep_ids: report.sweepIds,
    sections: sectionsToManifest(report.sections),
    status: report.status,
  };
}

export type ReportPatch = Partial<
  Pick<Report, "title" | "sections" | "runIds" | "sweepIds" | "status">
>;

export function reportPatchToApi(patch: ReportPatch): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (patch.title !== undefined) out.title = patch.title;
  if (patch.runIds !== undefined) out.run_ids = patch.runIds;
  if (patch.sweepIds !== undefined) out.sweep_ids = patch.sweepIds;
  if (patch.sections !== undefined) out.sections = sectionsToManifest(patch.sections);
  if (patch.status !== undefined) out.status = patch.status;
  return out;
}
