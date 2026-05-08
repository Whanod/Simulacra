import { describe, it, expect } from "vitest";
import type { Report, ReportSection } from "@/lib/types/reports";
import {
  fromApiReport,
  fromApiReports,
  manifestToSections,
  reportPatchToApi,
  reportToApi,
  sectionsToManifest,
  type ApiReportDetailResponse,
  type ApiReportManifest,
  type ApiReportRow,
} from "@/lib/api/adapters/reports";

const MANIFEST: ApiReportManifest = {
  title: "Contract analysis",
  description: null,
  run_ids: ["run-a", "run-b"],
  sweep_ids: ["sweep-x"],
  charts: [],
  exports: [],
  raw_artifacts: ["spec", "result"],
  sections: [
    { id: "s1", type: "summary", title: "Overview", content: "top-level" },
    { id: "s2", type: "chart", title: "Price", runId: "run-a" },
    { id: "s3", type: "metrics", title: "Metrics", runId: "run-b" },
  ],
};

const LIST_ROW: ApiReportRow = {
  report_id: "rep-1",
  status: "draft",
  created_at: "2026-04-10T10:00:00Z",
  updated_at: "2026-04-10T10:05:00Z",
  has_bundle: false,
  manifest: MANIFEST,
};

const DETAIL: ApiReportDetailResponse = {
  report: {
    report_id: "rep-1",
    status: "published",
    created_at: "2026-04-10T10:00:00Z",
    updated_at: "2026-04-10T11:00:00Z",
    has_bundle: true,
  },
  manifest: MANIFEST,
};

describe("reports adapter", () => {
  describe("manifestToSections / sectionsToManifest round-trip", () => {
    it("round-trips a 3-section manifest", () => {
      const sections = manifestToSections(MANIFEST);
      expect(sections).toHaveLength(3);
      expect(sections[0].type).toBe("summary");
      expect(sections[1].runId).toBe("run-a");
      expect(sections[2].type).toBe("metrics");

      const serialized = sectionsToManifest(sections);
      expect(serialized).toHaveLength(3);
      const roundTripped = manifestToSections({ sections: serialized });
      expect(roundTripped).toEqual(sections);
    });

    it("filters out malformed section entries", () => {
      const out = manifestToSections({
        sections: [
          { id: "ok", type: "summary", title: "Hi" },
          "not an object",
          null,
          { id: "bad", type: "weird", title: "x" },
          { id: "chart", type: "chart", title: "C", runId: "run-z" },
        ],
      });
      expect(out.map((s) => s.id)).toEqual(["ok", "chart"]);
    });

    it("returns [] when manifest has no sections", () => {
      expect(manifestToSections({ title: "no sections" })).toEqual([]);
      expect(manifestToSections(null)).toEqual([]);
      expect(manifestToSections(undefined)).toEqual([]);
    });
  });

  describe("fromApiReport", () => {
    it("maps a list row to Report", () => {
      const report = fromApiReport(LIST_ROW);
      expect(report.id).toBe("rep-1");
      expect(report.title).toBe("Contract analysis");
      expect(report.status).toBe("draft");
      expect(report.sections).toHaveLength(3);
      expect(report.runIds).toEqual(["run-a", "run-b"]);
      expect(report.sweepIds).toEqual(["sweep-x"]);
    });

    it("maps a detail {report, manifest} response to Report", () => {
      const report = fromApiReport(DETAIL);
      expect(report.id).toBe("rep-1");
      expect(report.status).toBe("published");
      expect(report.sections).toHaveLength(3);
    });

    it("tolerates a missing manifest (placeholder title)", () => {
      const report = fromApiReport({ report_id: "rep-2" } as ApiReportRow);
      expect(report.id).toBe("rep-2");
      expect(report.title).toBe("Untitled report");
      expect(report.sections).toEqual([]);
      expect(report.runIds).toEqual([]);
    });

    it("maps 'ready' status to draft (frontend only tracks draft/published)", () => {
      const report = fromApiReport({
        report_id: "rep-3",
        status: "ready",
      });
      expect(report.status).toBe("draft");
    });
  });

  describe("fromApiReports", () => {
    it("maps a list response", () => {
      const list = fromApiReports([LIST_ROW]);
      expect(list).toHaveLength(1);
      expect(list[0].id).toBe("rep-1");
    });
  });

  describe("reportToApi / reportPatchToApi", () => {
    const REPORT: Pick<Report, "title" | "sections" | "runIds" | "sweepIds" | "status"> = {
      title: "Test",
      runIds: ["run-1"],
      sweepIds: [],
      status: "draft",
      sections: [{ id: "s1", type: "notes", title: "Todo" }] as ReportSection[],
    };

    it("reportToApi flattens to backend keys", () => {
      const body = reportToApi(REPORT);
      expect(body.title).toBe("Test");
      expect(body.run_ids).toEqual(["run-1"]);
      expect(body.sweep_ids).toEqual([]);
      expect(body.sections).toEqual([{ id: "s1", type: "notes", title: "Todo" }]);
      expect(body.status).toBe("draft");
    });

    it("reportPatchToApi includes only defined keys", () => {
      const body = reportPatchToApi({ title: "New" });
      expect(Object.keys(body)).toEqual(["title"]);
      expect(body.title).toBe("New");
    });

    it("reportPatchToApi serializes sections when provided", () => {
      const body = reportPatchToApi({
        sections: [{ id: "sx", type: "chart", title: "x", runId: "r" }],
      });
      expect(body.sections).toEqual([
        { id: "sx", type: "chart", title: "x", runId: "r" },
      ]);
    });
  });
});
