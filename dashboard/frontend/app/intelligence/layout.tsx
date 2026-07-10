"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard } from "lucide-react";

/**
 * Portfolio Intelligence Layer — section shell.
 * A Bloomberg-style sub-navigation across the PIL dashboards. Tabs light up as
 * each capability ships; unimplemented ones render as muted "soon" chips so the
 * roadmap is visible without dead links.
 */
const TABS: { href: string; label: string; enabled: boolean }[] = [
  { href: "/intelligence", label: "Overview", enabled: true },
  { href: "/intelligence/risk", label: "Risk & Exposure", enabled: true },
  { href: "/intelligence/scorecards", label: "Scorecards", enabled: false },
  { href: "/intelligence/analytics", label: "Analytics", enabled: false },
  { href: "/intelligence/allocation", label: "Allocation", enabled: false },
  { href: "/intelligence/health", label: "Health", enabled: false },
  { href: "/intelligence/reports", label: "Reports", enabled: false },
  { href: "/intelligence/alerts", label: "Alerts", enabled: false },
];

export default function IntelligenceLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();

  return (
    <div className="min-h-screen">
      {/* Header */}
      <div className="border-b border-cyan-500/10 bg-slate-900/40 backdrop-blur-sm sticky top-0 z-40">
        <div className="px-4 md:px-8 pt-5 pb-0 max-w-[1600px] mx-auto">
          <div className="flex items-center gap-2.5 mb-1">
            <div
              className="w-8 h-8 rounded-lg grid place-items-center shrink-0"
              style={{ background: "var(--accent-dim)", border: "1px solid var(--accent)" }}
            >
              <LayoutDashboard size={16} color="var(--accent)" />
            </div>
            <div>
              <h1 className="neon-text text-base md:text-lg font-bold leading-tight">
                Portfolio Intelligence
              </h1>
              <p className="text-[0.62rem] tracking-wide" style={{ color: "var(--text-dim)" }}>
                MULTI-ENGINE PMS · OBSERVE · MEASURE · REPORT
              </p>
            </div>
          </div>

          {/* Tabs */}
          <div className="flex items-center gap-1 overflow-x-auto -mx-1 px-1 pt-2">
            {TABS.map((t) => {
              const active = t.href === path;
              if (!t.enabled) {
                return (
                  <span
                    key={t.href}
                    className="px-3 py-2 text-[0.72rem] whitespace-nowrap rounded-t-md opacity-40 cursor-not-allowed"
                    style={{ color: "var(--text-dim)" }}
                    title="Coming soon"
                  >
                    {t.label}
                  </span>
                );
              }
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  className={`px-3 py-2 text-[0.72rem] whitespace-nowrap rounded-t-md border-b-2 transition-colors ${
                    active
                      ? "border-[var(--accent)] text-[var(--accent)]"
                      : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  }`}
                >
                  {t.label}
                </Link>
              );
            })}
          </div>
        </div>
      </div>

      <div className="px-4 md:px-8 py-6 max-w-[1600px] mx-auto">{children}</div>
    </div>
  );
}
