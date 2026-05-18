import { describe, it, expect } from "vitest";
import { replayService } from "@/lib/services/replayService";

// PRD US-002 line 333: POST /v1/replay returns run_id + decoded share metadata.
// The committed entry-gate fixture lives at slot 420_196_842.
const CORPUS_SLOT = 420_196_842;

describe("replayService (integration)", () => {
  it("submitReplay returns a run id and metadata for a valid slot range", async () => {
    const result = await replayService.submitReplay({
      slotStart: CORPUS_SLOT,
      slotEnd: CORPUS_SLOT,
    });
    expect(result.runId).toBeTruthy();
    expect(result.slotRange).toEqual([CORPUS_SLOT, CORPUS_SLOT]);
    expect(typeof result.decodedTransactionShare).toBe("number");
    expect(Array.isArray(result.unsupportedProgramIds)).toBe(true);
    expect(typeof result.eligibleForCalibration).toBe("boolean");
    expect(result.replayMetrics).toMatchObject({
      bundle_landing_rate: expect.any(Object),
      tip_efficiency: expect.any(Object),
      slot_inclusion_latency: expect.any(Object),
      cu_per_dollar_tip_breakeven: expect.any(Object),
      skip_rate_cost: expect.any(Object),
      write_lock_heatmap: expect.any(Object),
      submission_path_comparison: expect.any(Object),
    });
    expect(result.replayDiff?.per_metric_error).toMatchObject({
      bundle_landing_rate: expect.any(Object),
      tip_efficiency: expect.any(Object),
      tips_paid: expect.any(Object),
    });
    expect(result.replayDiff?.per_metric_error?.bundle_landing_rate?.threshold).toBe(
      0.05,
    );
  });

  it("submitReplay forwards counterfactual specs", async () => {
    const result = await replayService.submitReplay({
      slotStart: CORPUS_SLOT,
      slotEnd: CORPUS_SLOT,
      counterfactuals: [
        {
          kind: "TipReplaceCounterfactual",
          params: { target_bundle_id: "b-1", new_tip_lamports: 0 },
        },
      ],
    });
    expect(result.counterfactuals).toHaveLength(1);
    expect(result.counterfactuals[0]?.kind).toBe("TipReplaceCounterfactual");
  });
});
