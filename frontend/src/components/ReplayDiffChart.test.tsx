import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import ReplayDiffChart, { type ReplayDiffMetric } from "./ReplayDiffChart";

let container: HTMLDivElement | null = null;

function renderChart(metrics: ReplayDiffMetric[]) {
  container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(createElement(ReplayDiffChart, { metrics }));
  });

  return root;
}

afterEach(() => {
  document.body.innerHTML = "";
  container = null;
});

describe("ReplayDiffChart", () => {
  it("renders_actual_vs_counterfactual_lines", () => {
    const root = renderChart([
      {
        key: "landing_probability",
        label: "Landing probability",
        actual: 25,
        counterfactual: 50,
        unit: "%",
        fractionDigits: 1,
      },
    ]);

    const metric = container?.querySelector<HTMLElement>(
      '[data-testid="replay-diff-metric"]',
    );
    const chartImage = metric?.querySelector<SVGSVGElement>(
      'svg[role="img"]',
    );
    const connector = metric?.querySelector<SVGLineElement>(
      ".replay-diff-connector",
    );
    const actualDot = metric?.querySelector<SVGCircleElement>(
      ".replay-diff-actual-dot",
    );
    const counterfactualDot = metric?.querySelector<SVGCircleElement>(
      ".replay-diff-counterfactual-dot",
    );

    expect(metric?.dataset.metricKey).toBe("landing_probability");
    expect(chartImage?.getAttribute("aria-label")).toBe(
      "Landing probability: actual 25.0%, counterfactual 50.0%",
    );
    expect(connector?.getAttribute("x1")).toBe("50");
    expect(connector?.getAttribute("x2")).toBe("92");
    expect(connector?.getAttribute("y1")).toBe("18");
    expect(connector?.getAttribute("y2")).toBe("46");
    expect(actualDot?.getAttribute("cx")).toBe("50");
    expect(actualDot?.getAttribute("cy")).toBe("18");
    expect(counterfactualDot?.getAttribute("cx")).toBe("92");
    expect(counterfactualDot?.getAttribute("cy")).toBe("46");
    expect(metric?.textContent).toContain("+25.0%");
    expect(metric?.textContent).toContain("Actual 25.0%");
    expect(metric?.textContent).toContain("Counterfactual 50.0%");

    act(() => {
      root.unmount();
    });
  });
});
