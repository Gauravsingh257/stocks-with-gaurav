/**
 * Proxy /api/kite/login to the FastAPI backend (Railway).
 * Visiting https://stockswithgaurav.com/api/kite/login redirects to the backend, then to Zerodha.
 * Prefer NEXT_PUBLIC_BACKEND_URL for frontend; fallback to BACKEND_URL.
 */
import { NextResponse } from "next/server";

export function GET() {
  const backend =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.BACKEND_URL ||
    "";
  const base = (typeof backend === "string" && backend) ? backend.replace(/\/$/, "") : "";
  const url = base ? `${base}/api/kite/login` : "";

  if (!url) {
    return NextResponse.json(
      {
        error: "Backend URL not configured. Set NEXT_PUBLIC_BACKEND_URL in Vercel.",
      },
      { status: 503 }
    );
  }

  return NextResponse.redirect(url);
}
