import { describe, it, expect } from "vitest";
import { calibrationBandView } from "./calibrationBandView";

describe("calibrationBandView", () => {
  it("hides the band when input is null", () => {
    expect(calibrationBandView(null).visible).toBe(false);
  });

  it("hides the band when the run is not a calibrated replay", () => {
    const v = calibrationBandView({
      isCalibratedReplay: false,
      predicted: 2.41,
      actual: 2.39,
      supported: true,
      threshold: { relative: 0.005 },
    });
    expect(v.visible).toBe(false);
  });

  it("hides the band when predicted is not a finite number", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: null,
      actual: 2.39,
      supported: true,
      threshold: { relative: 0.005 },
    });
    expect(v.visible).toBe(false);
  });

  it("renders the PRD-line-784 example shape when within threshold", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 2.41,
      actual: 2.39,
      supported: true,
      threshold: { relative: 0.05 }, // 5% — well above the 0.84% delta
      fractionDigits: 2,
    });
    expect(v.visible).toBe(true);
    expect(v.modelText).toBe("Model: 2.41");
    expect(v.mainnetText).toBe("Mainnet: 2.39");
    expect(v.deltaText).toBe("Δ = +0.02");
    expect(v.relErrorText).toBe("(0.84%)");
    expect(v.withinThreshold).toBe(true);
    expect(v.breached).toBe(false);
    expect(v.marker).toBe("✓");
    expect(v.thresholdText).toBe("within 5.00% threshold");
    expect(v.composed).toContain("Model: 2.41");
    expect(v.composed).toContain("Mainnet: 2.39");
    expect(v.composed).toContain("Δ = +0.02");
    expect(v.composed).toContain("(0.84%)");
    expect(v.composed).toContain("within 5.00% threshold");
    expect(v.composed).toContain("✓");
  });

  it("flags out-of-band when relative error exceeds the relative threshold", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 3.0,
      actual: 2.0,
      supported: true,
      threshold: { relative: 0.005 },
      fractionDigits: 2,
    });
    expect(v.visible).toBe(true);
    expect(v.withinThreshold).toBe(false);
    expect(v.breached).toBe(true);
    expect(v.marker).toBe("✗");
    expect(v.thresholdText).toMatch(/out-of-band/);
  });

  it("uses absolute threshold for count metrics like liquidations_triggered", () => {
    const within = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 4,
      actual: 5,
      supported: true,
      threshold: { absolute: 1 },
      fractionDigits: 0,
    });
    expect(within.withinThreshold).toBe(true);
    expect(within.thresholdText).toBe("within ±1 threshold");

    const breached = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 4,
      actual: 8,
      supported: true,
      threshold: { absolute: 1 },
      fractionDigits: 0,
    });
    expect(breached.breached).toBe(true);
    expect(breached.thresholdText).toBe("out-of-band ±1 threshold");
  });

  it("marks the band as 'decoder pending' when actual is unsupported", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 2.41,
      actual: null,
      supported: false,
      threshold: { relative: 0.005 },
    });
    expect(v.visible).toBe(true);
    expect(v.mainnetText).toContain("—");
    expect(v.deltaText).toBe("—");
    expect(v.relErrorText).toBe("");
    expect(v.withinThreshold).toBe(false);
    expect(v.breached).toBe(false);
    expect(v.marker).toBe("⏳");
    expect(v.thresholdText).toBe("decoder pending");
  });

  it("falls back to 'no threshold configured' when neither relative nor absolute set", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 2.41,
      actual: 2.39,
      supported: true,
      threshold: null,
    });
    expect(v.visible).toBe(true);
    expect(v.thresholdText).toBe("no threshold configured");
    expect(v.withinThreshold).toBe(false);
    expect(v.breached).toBe(false);
    expect(v.marker).toBe("");
  });

  it("guards against actual=0 by leaving relative error blank", () => {
    const v = calibrationBandView({
      isCalibratedReplay: true,
      predicted: 0.5,
      actual: 0,
      supported: true,
      threshold: { absolute: 1 },
      fractionDigits: 2,
    });
    expect(v.visible).toBe(true);
    expect(v.relErrorText).toBe("");
    // |0.5 - 0| = 0.5, threshold ±1 -> within
    expect(v.withinThreshold).toBe(true);
  });
});
