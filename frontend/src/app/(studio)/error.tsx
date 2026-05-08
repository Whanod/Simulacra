"use client";

export default function StudioError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <>
      <header id="topbar">
        <h2 style={{ color: "var(--red)" }}>Error</h2>
        <div className="topbar-actions" />
      </header>
      <div id="content" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div style={{ fontSize: "2rem", fontWeight: 700, color: "var(--red)", marginBottom: 12 }}>
            Something went wrong
          </div>
          <p style={{ color: "var(--text-2)", marginBottom: 8, fontSize: ".85rem" }}>
            {error.message}
          </p>
          <button className="btn btn-primary" onClick={reset}>
            Try Again
          </button>
        </div>
      </div>
    </>
  );
}
