interface StatCardProps {
  label: string;
  value: string | number;
  delta?: string;
  deltaDir?: "up" | "down";
  deltaColor?: string;
  hint?: string;
  valueColor?: string;
  valueSize?: string;
  style?: React.CSSProperties;
  onClick?: () => void;
  children?: React.ReactNode;
}

export default function StatCard({
  label,
  value,
  delta,
  deltaDir,
  deltaColor,
  hint,
  valueColor,
  valueSize,
  style,
  onClick,
  children,
}: StatCardProps) {
  return (
    <div className="stat-card" style={{ ...style, cursor: onClick ? "pointer" : undefined }} onClick={onClick}>
      <span className="label">{label}</span>
      <span className="value" style={{ color: valueColor, fontSize: valueSize }}>
        {value}
      </span>
      {delta && (
        <span
          className={`delta${deltaDir ? ` ${deltaDir}` : ""}`}
          style={deltaColor ? { color: deltaColor } : undefined}
        >
          {delta}
        </span>
      )}
      {hint && <span className="hint">{hint}</span>}
      {children}
    </div>
  );
}
