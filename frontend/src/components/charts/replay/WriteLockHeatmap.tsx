"use client";

import type { CSSProperties } from "react";
import EmptyReplayChart from "./EmptyReplayChart";
import ReplayChartFrame from "./ReplayChartFrame";
import type { CalibrationBandInput } from "@/components/CalibrationBand";
import type { WriteLockHeatmapMetric } from "./types";

function shortAccount(account: string): string {
  return account.length > 14 ? `${account.slice(0, 6)}…${account.slice(-4)}` : account;
}

export default function WriteLockHeatmap({
  metric,
  calibrationBand,
}: {
  metric?: WriteLockHeatmapMetric;
  calibrationBand?: CalibrationBandInput | null;
}) {
  const accounts = metric?.accounts ?? [];
  const slots = metric?.slots ?? [];
  const countMap = new Map<string, number>();
  for (const cell of metric?.counts ?? []) {
    countMap.set(`${cell.account}:${cell.slot}`, cell.count);
  }
  const max = Math.max(1, metric?.max_contention ?? 0);

  return (
    <ReplayChartFrame
      title="Write-lock contention"
      value={metric ? `${metric.max_contention ?? metric.value} max` : "—"}
      sampleSize={metric?.sample_size}
      empty={!metric}
      calibrationBand={calibrationBand}
    >
      {metric && accounts.length > 0 && slots.length > 0 ? (
        <div
          className="replay-heatmap"
          role="img"
          aria-label="Write-lock contention heatmap"
          style={{ "--replay-heatmap-cols": String(slots.length) } as CSSProperties}
        >
          <div className="corner" />
          {slots.map((slot) => (
            <div key={slot} className="slot-label">{slot}</div>
          ))}
          {accounts.map((account) => (
            <div key={account} className="heatmap-row" style={{ display: "contents" }}>
              <div className="account-label" title={account}>{shortAccount(account)}</div>
              {slots.map((slot) => {
                const count = countMap.get(`${account}:${slot}`) ?? 0;
                const alpha = count === 0 ? 0.08 : 0.18 + (count / max) * 0.72;
                return (
                  <div
                    key={`${account}-${slot}`}
                    className="heatmap-cell"
                    style={{ background: `rgba(108, 138, 255, ${alpha})` }}
                    title={`${account} @ ${slot}: ${count} locks`}
                  >
                    {count > 0 ? count : ""}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      ) : (
        <EmptyReplayChart />
      )}
    </ReplayChartFrame>
  );
}
