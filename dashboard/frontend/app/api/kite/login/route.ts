/**
 * Redirect /api/kite/login to the FastAPI backend (Railway).
 * Visiting stockswithgaurav.com/api/kite/login will send the user to the backend login URL.
 *
 * Requires BACKEND_URL or NEXT_PUBLIC_BACKEND_URL to be set in Vercel.
 */
import { NextResponse } from "next/server";

export function GET() {
  const backend =
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "";
  const url = backend ? `${backend.replace(/\/$/, "")}/api/kite/login` : "";

  if (!url) {
    return NextResponse.json(
      {
        error: "Backend URL not configured",
        hint: "Set BACKEND_URL and NEXT_PUBLIC_BACKEND_URL in Vercel to your Railway backend URL.",
      },
      { status: 503 }
    );
  }

  return NextResponse.redirect(url);
}
