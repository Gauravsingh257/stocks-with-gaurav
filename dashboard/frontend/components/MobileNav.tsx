"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, Globe, Sparkles, Bookmark, LayoutDashboard } from "lucide-react";
import { useAuth } from "@/lib/auth";

// Default ON (live); set NEXT_PUBLIC_PIL_ENABLED=0 to hide.
const PIL_ENABLED = process.env.NEXT_PUBLIC_PIL_ENABLED !== "0";

// Mirrors the desktop Sidebar: 5 grouped destinations. Folded pages are reached
// via <SectionTabs> inside each group.
const ITEMS = [
  { href: "/terminal", label: "Terminal", icon: Sparkles },
  { href: "/research", label: "Research", icon: Bot, match: ["/research", "/screeners"] },
  { href: "/watchlist", label: "Watchlist", icon: Bookmark, auth: true },
  ...(PIL_ENABLED
    ? [{ href: "/intelligence", label: "Portfolio", icon: LayoutDashboard, auth: true, match: ["/intelligence", "/analytics", "/journal"] }]
    : [{ href: "/analytics", label: "Portfolio", icon: LayoutDashboard, match: ["/analytics", "/journal"] }]),
  { href: "/oi-intelligence", label: "Markets", icon: Globe, match: ["/oi-intelligence", "/market-intelligence"] },
] as { href: string; label: string; icon: typeof LayoutDashboard; auth?: boolean; match?: string[] }[];

export default function MobileNav() {
  const path = usePathname();
  const { user } = useAuth();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 md:hidden glass border-t border-cyan-500/20 overflow-x-auto"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0)" }}
    >
      <div className="flex items-center justify-start gap-0 min-w-max h-14 min-h-[44px] px-1">
        {ITEMS.filter((it) => !it.auth || user).map(({ href, label, icon: Icon, match }) => {
          const prefixes = match ?? [href];
          const active = prefixes.some((p) => path === p || (p !== "/" && path.startsWith(p)));
          return (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center justify-center gap-0.5 shrink-0 w-14 h-full text-[0.6rem] font-medium transition-colors ${
                active
                  ? "text-[var(--accent)]"
                  : "text-[var(--text-secondary)]"
              }`}
              aria-label={label}
            >
              <Icon size={18} strokeWidth={2} />
              <span className="truncate max-w-[52px]">{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
