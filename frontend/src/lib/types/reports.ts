export interface ReportSection {
  id: string;
  type: "summary" | "chart" | "metrics" | "agents" | "notes" | "export";
  title: string;
  content?: string;
  runId?: string;
  sweepId?: string;
}

export interface Report {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  sections: ReportSection[];
  runIds: string[];
  sweepIds: string[];
  status: "draft" | "published";
}
