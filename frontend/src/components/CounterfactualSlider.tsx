type CounterfactualSliderProps = {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  disabled?: boolean;
  sliderTestId?: string;
  inputTestId?: string;
  onChange: (value: number) => void;
};

function coerceNumber(value: string, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return min;
  return Math.min(max, Math.max(min, parsed));
}

export default function CounterfactualSlider({
  id,
  label,
  value,
  min,
  max,
  step,
  unit,
  disabled = false,
  sliderTestId,
  inputTestId,
  onChange,
}: CounterfactualSliderProps) {
  const safeValue = Math.min(max, Math.max(min, value));

  return (
    <div className="counterfactual-slider">
      <div className="counterfactual-slider-heading">
        <label htmlFor={id}>{label}</label>
        {unit ? <span>{unit}</span> : null}
      </div>
      <div className="counterfactual-slider-controls">
        <input
          id={id}
          data-testid={sliderTestId}
          type="range"
          min={min}
          max={max}
          step={step}
          value={safeValue}
          disabled={disabled}
          onChange={(event) => onChange(coerceNumber(event.target.value, min, max))}
        />
        <input
          aria-label={`${label} value`}
          data-testid={inputTestId}
          type="number"
          min={min}
          max={max}
          step={step}
          value={safeValue}
          disabled={disabled}
          onChange={(event) => onChange(coerceNumber(event.target.value, min, max))}
        />
      </div>
    </div>
  );
}
