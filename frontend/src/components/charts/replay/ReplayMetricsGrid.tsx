"use client";

import type { ReactNode } from "react";

import BundleLandingRateChart from "./BundleLandingRateChart";
import CuTipBreakEvenCurve from "./CuTipBreakEvenCurve";
import SkipRateCostChart from "./SkipRateCostChart";
import SlotInclusionLatencyChart from "./SlotInclusionLatencyChart";
import SubmissionPathComparisonChart from "./SubmissionPathComparisonChart";
import TipEfficiencyChart from "./TipEfficiencyChart";
import WriteLockHeatmap from "./WriteLockHeatmap";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import {
  REPLAY_METRIC_ORDER,
  type ReplayMetricKey,
  type ReplayMetrics,
  normalizeReplayMetrics,
} from "./types";

interface ReplayMetricsGridProps {
  metrics: ReplayMetrics | unknown;
  metricKeys?: ReplayMetricKey[];
  calibrationBands?: Partial<Record<ReplayMetricKey, CalibrationBandInput | null>>;
  embedActionForMetric?: (metricKey: ReplayMetricKey) => ReactNode;
}

export default function ReplayMetricsGrid({
  metrics,
  metricKeys = REPLAY_METRIC_ORDER,
  calibrationBands,
  embedActionForMetric,
}: ReplayMetricsGridProps) {
  const normalized = normalizeReplayMetrics(metrics);

  return (
    <div className="replay-metrics-grid" data-testid="replay-metrics-grid">
      {metricKeys.map((key) => {
        let chart: ReactNode = null;
        switch (key) {
          case "bundle_landing_rate":
            chart = (
              <BundleLandingRateChart
                metric={normalized.bundle_landing_rate}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "tip_efficiency":
            chart = (
              <TipEfficiencyChart
                metric={normalized.tip_efficiency}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "slot_inclusion_latency":
            chart = (
              <SlotInclusionLatencyChart
                metric={normalized.slot_inclusion_latency}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "cu_per_dollar_tip_breakeven":
            chart = (
              <CuTipBreakEvenCurve
                metric={normalized.cu_per_dollar_tip_breakeven}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "skip_rate_cost":
            chart = (
              <SkipRateCostChart
                metric={normalized.skip_rate_cost}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "write_lock_heatmap":
            chart = (
              <WriteLockHeatmap
                metric={normalized.write_lock_heatmap}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          case "submission_path_comparison":
            chart = (
              <SubmissionPathComparisonChart
                metric={normalized.submission_path_comparison}
                calibrationBand={calibrationBands?.[key]}
              />
            );
            break;
          default:
            return null;
        }
        const action = embedActionForMetric?.(key);
        return (
          <div className="replay-chart-embed-wrap" key={key}>
            {action ? <div className="replay-chart-embed-action">{action}</div> : null}
            {chart}
          </div>
        );
      })}
    </div>
  );
}
