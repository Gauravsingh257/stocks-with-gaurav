"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * SectionTabs — a lightweight sub-navigation strip that keeps a *group* of
 * related pages one click apart after the primary sidebar was consolidated
 * from 8 flat items to 5 grouped destinations (Terminal · Research ·
 * Watchlist · Portfolio · Markets).
 *
 * Example: the "Markets" group renders this with [OI Radar, Market Intel] so
 * neither page is orphaned once it leaves the top-level sidebar.
 */
export type SectionTab = { href: string; label: string };

export default function SectionTabs({
  items,
  label,
}: {
  items: SectionTab[];
  label?: string;
}) {
  const path = usePathname();
  return (
    <nav
      aria-label={label ? `${label} section` : "Section"}
      className="flex items-center gap-1 overflow-x-auto -mx-1 px-1 mb-4 border-b border-cyan-500/10"
    >
      {label && (
        <span
          className="text-[0.6rem] font-semibold uppercase tracking-[0.14em] pr-2 shrink-0"
          style={{ color: "var(--text-dim)" }}
        >
          {label}
        </span>
      )}
      {items.map((t) => {
        const active = path === t.href || (t.href !== "/" && path.startsWith(t.href));
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`px-3 py-2 text-[0.75rem] whitespace-nowrap rounded-t-md border-b-2 transition-colors ${
              active
                ? "border-[var(--accent)] text-[var(--accent)] font-semibold"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
