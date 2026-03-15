"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, TrendingUp, Bot, BookOpen, Settings } from "lucide-react";

const ITEMS = [
  { href: "/live", label: "Home", icon: Activity },
  { href: "/charts", label: "Charts", icon: TrendingUp },
  { href: "/research", label: "AI", icon: Bot },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/chat", label: "Settings", icon: Settings },
];

export default function MobileNav() {
  const path = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 md:hidden glass border-t border-cyan-500/20"
      style={{ paddingBottom: "env(safe-area-inset-bottom, 0)" }}
    >
      <div className="flex items-center justify-around h-14 min-h-[44px]">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active =
            path === href || (href !== "/" && path.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center justify-center gap-0.5 flex-1 min-w-0 h-full text-[0.65rem] font-medium transition-colors ${
                active
                  ? "text-[var(--accent)]"
                  : "text-[var(--text-secondary)]"
              }`}
              aria-label={label}
            >
              <Icon size={20} strokeWidth={2} />
              <span className="truncate max-w-full px-1">{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
