import Link from "next/link";

export default function StudioNotFound() {
  return (
    <>
      <header id="topbar">
        <h2>Not Found</h2>
        <div className="topbar-actions" />
      </header>
      <div id="content" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: "3rem", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-2)", marginBottom: 12 }}>
            404
          </div>
          <p style={{ color: "var(--text-2)", marginBottom: 20 }}>
            The page you&apos;re looking for doesn&apos;t exist.
          </p>
          <Link href="/dashboard" className="btn btn-primary">
            Back to Dashboard
          </Link>
        </div>
      </div>
    </>
  );
}
