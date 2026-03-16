"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart2, BookOpen, Bot, Eye, MessageSquare, TrendingUp } from "lucide-react";

const ITEMS = [
  { href: "/live", label: "Live", icon: Activity },
  { href: "/analytics", label: "Analytics", icon: BarChart2 },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/research", label: "Research", icon: Bot },
  { href: "/oi-intelligence", label: "OI", icon: Eye },
  { href: "/charts", label: "Charts", icon: TrendingUp },
  { href: "/chat", label: "Chat", icon: MessageSquare },
];

export default function MobileNav() {
  const path = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 md:hidden glass border-t border-cyan-500/20 overflow-x-auto"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0)" }}
    >
      <div className="flex items-center justify-start gap-0 min-w-max h-14 min-h-[44px] px-1">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active =
            path === href || (href !== "/" && path.startsWith(href));
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
