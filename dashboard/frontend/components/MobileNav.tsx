"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { NAV } from "@/lib/navGroups";

/**
 * Mobile bottom bar — the top-level sections from the shared NAV config (same
 * source of truth as the desktop Sidebar, so the two can't drift). Each section's
 * children are reached by opening the hamburger drawer, which renders the full
 * hierarchical Sidebar.
 */
export default function MobileNav() {
  const path = usePathname() || "";
  const { user } = useAuth();

  const items = NAV.filter((s) => (!s.auth || user) && !s.admin);

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 md:hidden glass border-t border-cyan-500/20 overflow-x-auto"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0)" }}
      aria-label="Primary"
    >
      <div className="flex items-center justify-start gap-0 min-w-max h-14 min-h-[44px] px-1">
        {items.map(({ href, label, icon: Icon, match }) => {
          const prefixes = [href, ...(match ?? [])];
          const active = prefixes.some((p) => path === p || (p !== "/" && path.startsWith(p)));
          return (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center justify-center gap-0.5 shrink-0 w-14 h-full text-[0.6rem] font-medium transition-colors ${
                active ? "text-[var(--accent)]" : "text-[var(--text-secondary)]"
              }`}
              aria-label={label}
              aria-current={active ? "page" : undefined}
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
