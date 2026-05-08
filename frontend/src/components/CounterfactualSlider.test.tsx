import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import CounterfactualSlider from "./CounterfactualSlider";

let container: HTMLDivElement | null = null;

function renderSlider(onChange: (value: number) => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      createElement(CounterfactualSlider, {
        id: "tip-lamports",
        label: "Tip",
        value: 10,
        min: 0,
        max: 100,
        step: 5,
        sliderTestId: "tip-slider",
        onChange,
      }),
    );
  });

  return root;
}

function setNativeInputValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
}

afterEach(() => {
  document.body.innerHTML = "";
  container = null;
});

describe("CounterfactualSlider", () => {
  it("emits_change_event_with_new_value", () => {
    const onChange = vi.fn();
    const root = renderSlider(onChange);
    const slider = container?.querySelector<HTMLInputElement>(
      '[data-testid="tip-slider"]',
    );

    expect(slider).not.toBeNull();

    act(() => {
      setNativeInputValue(slider!, "75");
      slider!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(75);

    act(() => {
      root.unmount();
    });
  });
});
