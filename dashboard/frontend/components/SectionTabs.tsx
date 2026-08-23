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
 *
 * These read as *tabs*, not as a line of prose: the inactive ones previously
 * had a transparent background and a transparent bottom border, which made a
 * row of navigation look like a caption. Each item now has a hover state, the
 * active one is tinted and underlined, and `aria-current` tells a screen reader
 * which page it is on.
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
      className="tab-strip -mx-1 mb-4"
    >
      {label && <span className="tab-strip-label">{label}</span>}
      {items.map((t) => {
        const active = path === t.href || (t.href !== "/" && path.startsWith(t.href));
        return (
          <Link
            key={t.href}
            href={t.href}
            className="tab-item"
            aria-current={active ? "page" : undefined}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
