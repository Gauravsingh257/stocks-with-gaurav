import type { NextConfig } from "next";
import path from "path";

/**
 * BACKEND_URL (server-side only, never exposed to browser):
 *   - Local dev / Cloudflare tunnel: http://localhost:8000  (default)
 *   - Vercel production: set BACKEND_URL=https://<your-railway-app>.up.railway.app
 *
 * NEXT_PUBLIC_BACKEND_URL (baked into browser bundle at build time):
 *   - Leave empty to use Next.js proxy rewrites (good for local + tunnel)
 *   - Set to https://<your-railway-app>.up.railway.app on Vercel for direct
 *     browser → Railway calls (required because Vercel does not proxy WebSocket)
 *
 * NEXT_PUBLIC_WS_URL (WebSocket endpoint for the browser):
 *   - Leave empty for auto-detection (works on localhost and tunnel)
 *   - Set to wss://<your-railway-app>.up.railway.app/ws on Vercel
 */

const nextConfig: NextConfig = {
  turbopack: {
    root: path.resolve(__dirname),
  },

  allowedDevOrigins: ["*.trycloudflare.com"],

  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/ws",         destination: `${backend}/ws` },
    ];
  },
};

export default nextConfig;


