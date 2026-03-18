import type { NextConfig } from "next";
import path from "path";

/**
 * PRODUCTION (Vercel) — REQUIRED env vars:
 *   BACKEND_URL = https://<railway-web-service>.up.railway.app
 *   NEXT_PUBLIC_BACKEND_URL = same (for client-side REST + WS URL derivation)
 *   NEXT_PUBLIC_WS_URL = wss://<railway-web-service>.up.railway.app/ws
 *
 * If BACKEND_URL is unset in production, rewrites target localhost and API calls fail.
 * Vercel does NOT support WebSocket; /ws rewrite will not work — browser must connect
 * directly to Railway via NEXT_PUBLIC_WS_URL.
 */
const nextConfig: NextConfig = {
  turbopack: {
    root: path.resolve(__dirname),
  },

  allowedDevOrigins: ["*.trycloudflare.com"],

  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
    if (process.env.VERCEL && !process.env.BACKEND_URL) {
      console.warn("[next.config] BACKEND_URL is not set on Vercel — set it to your Railway Web URL or API will fail.");
    }
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/ws",         destination: `${backend}/ws` },
    ];
  },
};

export default nextConfig;


