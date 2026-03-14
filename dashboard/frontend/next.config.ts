import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  turbopack: {
    root: path.resolve(__dirname), // silence workspace root warning
  },

  // Allow Cloudflare tunnel origin in dev mode
  allowedDevOrigins: ["*.trycloudflare.com"],

  /**
   * Phase 6: API proxy rewrites
   * All /api/* requests are forwarded to the FastAPI backend (server-side).
   * This allows the tunnel to expose only port 3000 — API calls from a phone
   * hit https://your-tunnel.trycloudflare.com/api/... → Next.js rewrites →
   * http://localhost:8000/api/... on the same machine. Zero CORS issues.
   */
  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/ws",         destination: `${backend}/ws` },
    ];
  },
};

export default nextConfig;


