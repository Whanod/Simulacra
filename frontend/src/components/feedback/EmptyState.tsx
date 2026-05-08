import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

export default function EmptyState({ title, description, action, icon }: EmptyStateProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "3rem 1rem",
        gap: "0.75rem",
        textAlign: "center",
        color: "var(--muted, #9ca3af)",
      }}
    >
      {icon ? <div style={{ opacity: 0.6 }}>{icon}</div> : null}
      <div style={{ fontSize: "1rem", fontWeight: 600, color: "var(--fg, #e5e7eb)" }}>
        {title}
      </div>
      {description ? (
        <div style={{ fontSize: "0.875rem", maxWidth: "28rem" }}>{description}</div>
      ) : null}
      {action ? <div style={{ marginTop: "0.5rem" }}>{action}</div> : null}
    </div>
  );
}
