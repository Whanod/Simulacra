import type { Report } from "@/lib/types/reports";
import { apiFetch, apiFetchBlob } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import {
  fromApiReport,
  fromApiReports,
  reportPatchToApi,
  type ApiReportCreateResponse,
  type ApiReportDetailResponse,
  type ApiReportsListResponse,
  type ReportPatch,
} from "@/lib/api/adapters/reports";

export const reportService = {
  async listReports(): Promise<Report[]> {
    const resp = await apiFetch<ApiReportsListResponse>("/reports", {
      query: { limit: 100 },
    });
    return fromApiReports(resp.reports || []);
  },

  async getReport(id: string): Promise<Report | undefined> {
    try {
      const resp = await apiFetch<ApiReportDetailResponse>(`/reports/${id}`);
      return fromApiReport(resp);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return undefined;
      throw err;
    }
  },

  async createReport(title: string): Promise<Report> {
    const resp = await apiFetch<ApiReportCreateResponse>("/reports", {
      method: "POST",
      body: {
        title,
        run_ids: [],
        sweep_ids: [],
        charts: [],
        exports: [],
        raw_artifacts: ["spec", "result", "events", "rounds"],
        sections: [],
      },
    });
    return fromApiReport({
      report: {
        report_id: resp.report_id,
        status: "draft",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        has_bundle: false,
      },
      manifest: resp.manifest,
    });
  },

  async updateReport(id: string, patch: ReportPatch): Promise<Report> {
    const body = reportPatchToApi(patch);
    const resp = await apiFetch<ApiReportDetailResponse>(`/reports/${id}`, {
      method: "PUT",
      body,
    });
    return fromApiReport(resp);
  },

  async deleteReport(id: string): Promise<boolean> {
    try {
      await apiFetch(`/reports/${id}`, { method: "DELETE" });
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return false;
      throw err;
    }
  },

  async downloadBundle(id: string): Promise<Blob> {
    return apiFetchBlob(`/reports/${id}/bundle`);
  },
};
