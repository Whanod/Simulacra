export default function StudioLoading() {
  return (
    <>
      <header id="topbar">
        <h2 className="pulsing" style={{ color: "var(--text-2)" }}>Loading...</h2>
        <div className="topbar-actions" />
      </header>
      <div id="content" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", color: "var(--text-2)" }}>
          <div
            style={{
              width: 32,
              height: 32,
              border: "3px solid var(--border)",
              borderTopColor: "var(--accent)",
              borderRadius: "50%",
              animation: "spin 0.8s linear infinite",
              margin: "0 auto 12px",
            }}
          />
          <p>Loading...</p>
        </div>
      </div>
    </>
  );
}
