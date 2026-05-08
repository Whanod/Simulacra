import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CuTipBreakEvenCurve from "./replay/CuTipBreakEvenCurve";
import type { CuTipBreakEvenMetric } from "./replay/types";

function render(metric?: CuTipBreakEvenMetric): string {
  return renderToStaticMarkup(createElement(CuTipBreakEvenCurve, { metric }));
}

describe("CuTipBreakEvenCurve", () => {
  it("renders tip-paid versus extracted-value samples with the break-even line", () => {
    const html = render({
      value: 2 / 3,
      unit: "ratio",
      sample_size: 3,
      tips: [10_000, 20_000, 30_000],
      extracted_values: [12_000, 15_000, 30_000],
      ratios: [1.2, 0.75, 1],
    });

    expect(html).toContain("CU/$ tip break-even");
    expect(html).toContain("66.7% clear");
    expect(html).toContain("3 samples");
    expect(html).toContain('aria-label="Tip paid versus extracted value scatter"');
    expect(html).toContain('class="break-even"');
    expect(html.match(/class="cleared"/g)).toHaveLength(2);
    expect(html.match(/class="shortfall"/g)).toHaveLength(1);
    expect(html).toContain("tip 10.0k");
    expect(html).toContain("EV 12.0k");
    expect(html).toContain("tip paid");
    expect(html).toContain("extracted value");
  });

  it("renders the empty replay chart when no metric is available", () => {
    const html = render();

    expect(html).toContain('data-empty="true"');
    expect(html).toContain("No metric samples");
    expect(html).not.toContain("Tip paid versus extracted value scatter");
  });
});
