import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import WriteLockHeatmap from "./replay/WriteLockHeatmap";
import type { WriteLockHeatmapMetric } from "./replay/types";

function render(metric?: WriteLockHeatmapMetric): string {
  return renderToStaticMarkup(createElement(WriteLockHeatmap, { metric }));
}

describe("WriteLockHeatmap", () => {
  it("renders accounts, slots, and per-cell write-lock counts", () => {
    const html = render({
      value: 4,
      unit: "locks",
      sample_size: 3,
      accounts: ["AcctA1111", "AcctB2222"],
      slots: [420_196_842, 250_000_001],
      max_contention: 4,
      counts: [
        { account: "AcctA1111", slot: 420_196_842, count: 4 },
        { account: "AcctB2222", slot: 250_000_001, count: 2 },
      ],
    });

    expect(html).toContain("Write-lock contention");
    expect(html).toContain("4 max");
    expect(html).toContain("3 samples");
    expect(html).toContain('aria-label="Write-lock contention heatmap"');
    expect(html).toContain("--replay-heatmap-cols:2");
    expect(html).toContain("AcctA1111");
    expect(html).toContain("AcctB2222");
    expect(html).toContain("420196842");
    expect(html).toContain("250000001");
    expect(html.match(/class="heatmap-cell"/g)).toHaveLength(4);
    expect(html).toContain('title="AcctA1111 @ 420196842: 4 locks"');
    expect(html).toContain('title="AcctA1111 @ 250000001: 0 locks"');
    expect(html).toContain('title="AcctB2222 @ 250000001: 2 locks"');
    expect(html).toContain(">4</div>");
    expect(html).toContain(">2</div>");
  });

  it("renders the empty replay chart when no metric is available", () => {
    const html = render();

    expect(html).toContain('data-empty="true"');
    expect(html).toContain("No metric samples");
    expect(html).not.toContain("Write-lock contention heatmap");
  });
});
