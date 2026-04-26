"use client";

import Link from "next/link";
import { site } from "@/lib/site";

export default function PublicAuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden" style={{ background: "var(--bg-base)" }}>
      <header
        className="shrink-0 flex items-center justify-between gap-4 px-4 py-3 border-b"
        style={{
          borderColor: "rgba(6,182,212,0.15)",
          background: "rgba(15,23,42,0.85)",
          backdropFilter: "blur(10px)",
        }}
      >
        <Link
          href="/"
          className="text-sm font-semibold tracking-tight no-underline"
          style={{ color: "var(--text-primary)" }}
        >
          {site.name}
        </Link>
        <nav className="flex items-center gap-4 text-xs font-medium">
          <Link href="/research" className="no-underline" style={{ color: "var(--accent)" }}>
            Research
          </Link>
          <Link href="/" className="no-underline" style={{ color: "var(--text-secondary)" }}>
            Home
          </Link>
        </nav>
      </header>
      <div className="flex-1 flex flex-col min-h-0">{children}</div>
      <p
        className="shrink-0 text-center px-4 py-3"
        style={{
          fontSize: "0.62rem",
          lineHeight: 1.55,
          color: "rgba(255,255,255,0.32)",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(0,0,0,0.45)",
        }}
      >
        Educational use only. Not SEBI-registered. No buy/sell/hold recommendations. Market data may be delayed.
      </p>
    </div>
  );
}
