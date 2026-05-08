import { describe, it, expect } from "vitest";
import { reportService } from "@/lib/services/reportService";
import { simulationService } from "@/lib/services/simulationService";
import type { RunSpec } from "@/lib/types/simulations";
import type { ReportSection } from "@/lib/types/reports";

const DEFAULT_SPEC: RunSpec = {
  market: { type: "cfamm", num_assets: 2, initial_liquidity: 1_000_000, token_decimals: 9 },
  clock: { type: "block", block_time: 1, epoch_length: 1 },
  execution: { model: "direct", ordering: "fifo", cost_model: "zero" },
  fee_model: { type: "flat", rate_bps: 30 },
  agents: {
    total: 1,
    mix: {
      noise: 1,
      informed: 0,
      arbitrageur: 0,
      manipulator: 0,
      passive_lp: 0,
      rebalancing_lp: 0,
    },
    default_collateral: 1_000_000_000,
  },
  feeds: [
    { type: "stochastic", process: "gbm", drift: 0.0001, volatility: 0.02, initial_price: 1.0 },
  ],
  config: {
    num_rounds: 2,
    snapshot_interval: 1,
    seed: 201,
    numeric_mode: "FIXED_POINT",
    information_filter: "full_transparency",
  },
};

describe("reportService (integration)", () => {
  it("create → list → get → update → delete → get=undefined", async () => {
    const before = await reportService.listReports();

    const created = await reportService.createReport("Phase 2c smoke");
    expect(created.id).toBeTruthy();
    expect(created.title).toBe("Phase 2c smoke");
    expect(created.status).toBe("draft");

    const listAfterCreate = await reportService.listReports();
    expect(listAfterCreate.length).toBe(before.length + 1);
    expect(listAfterCreate.find((r) => r.id === created.id)).toBeDefined();

    const fetched = await reportService.getReport(created.id);
    expect(fetched).toBeDefined();
    expect(fetched!.title).toBe("Phase 2c smoke");

    const sections: ReportSection[] = [
      { id: "sec-overview", type: "summary", title: "Overview", content: "The big picture." },
      { id: "sec-chart", type: "chart", title: "Price", runId: "run-placeholder" },
    ];
    const updated = await reportService.updateReport(created.id, {
      title: "Phase 2c renamed",
      sections,
      runIds: ["run-placeholder"],
      status: "published",
    });
    expect(updated.title).toBe("Phase 2c renamed");
    expect(updated.status).toBe("published");
    expect(updated.sections).toHaveLength(2);
    expect(updated.sections[0].id).toBe("sec-overview");
    expect(updated.runIds).toEqual(["run-placeholder"]);

    const reread = await reportService.getReport(created.id);
    expect(reread!.title).toBe("Phase 2c renamed");
    expect(reread!.sections).toHaveLength(2);

    const deleted = await reportService.deleteReport(created.id);
    expect(deleted).toBe(true);
    expect(await reportService.getReport(created.id)).toBeUndefined();
  });

  it("deleteReport returns false for an unknown id (404 swallowed)", async () => {
    const result = await reportService.deleteReport("does-not-exist-xyz");
    expect(result).toBe(false);
  });

  it("updateReport only mutates the provided keys", async () => {
    const created = await reportService.createReport("Keep some");
    const firstUpdate = await reportService.updateReport(created.id, {
      runIds: ["r-a", "r-b"],
      sections: [{ id: "s1", type: "notes", title: "Todo" }],
    });
    expect(firstUpdate.runIds).toEqual(["r-a", "r-b"]);

    const secondUpdate = await reportService.updateReport(created.id, {
      title: "Only title changed",
    });
    expect(secondUpdate.title).toBe("Only title changed");
    expect(secondUpdate.runIds).toEqual(["r-a", "r-b"]); // preserved
    expect(secondUpdate.sections).toHaveLength(1); // preserved
  });

  it("downloadBundle returns a non-empty application/zip Blob", async () => {
    const { runId } = await simulationService.buildSpec(DEFAULT_SPEC);
    const report = await reportService.createReport("Bundle test");
    await reportService.updateReport(report.id, { runIds: [runId] });

    const blob = await reportService.downloadBundle(report.id);
    // Note: can't use toBeInstanceOf(Blob) because node-fetch's Blob and
    // jsdom's Blob are distinct constructors inside the vitest env.
    expect(blob).toBeDefined();
    expect(blob.type).toBe("application/zip");
    expect(blob.size).toBeGreaterThan(0);
  });
});
