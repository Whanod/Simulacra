import type { NextConfig } from "next";

// When the Next.js server should proxy `/api/*` to the internal FastAPI
// backend, set BACKEND_INTERNAL_URL at build time. In the all-in-one container
// this is always `http://127.0.0.1:8000`. For local dev the browser talks to
// the backend directly via NEXT_PUBLIC_API_URL, so this stays unset.
const backendInternalUrl = process.env.BACKEND_INTERNAL_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    if (!backendInternalUrl) return [];
    return [{ source: "/api/:path*", destination: `${backendInternalUrl}/:path*` }];
  },
};

export default nextConfig;
