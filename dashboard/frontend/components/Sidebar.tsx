"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen, BarChart2, Bot, TrendingUp, Eye, Globe
} from "lucide-react";
import { SidebarBotWidget } from "@/components/FuturisticElements";

const NAV = [
  { href: "/analytics",       label: "Analytics",       icon: BarChart2     },
  { href: "/journal",         label: "Journal",         icon: BookOpen      },
  { href: "/research",        label: "AI Research Center", icon: Bot        },
  { href: "/oi-intelligence", label: "OI Intelligence", icon: Eye           },
  { href: "/market-intelligence", label: "Market Intel", icon: Globe      },
  { href: "/charts",          label: "SMC Charts",      icon: TrendingUp    },
];

export default function Sidebar({
  isOpen = false,
  onClose,
}: {
  isOpen?: boolean;
  onClose?: () => void;
}) {
  const path = usePathname();

  return (
    <>
      {/* Mobile overlay */}
      {isOpen && (
        <button
          type="button"
          aria-label="Close menu"
          className="fixed inset-0 z-[99] bg-black/50 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Desktop: always visible; Mobile: drawer */}
      <aside
        className={`
          w-64 flex-shrink-0 flex flex-col py-6 px-3 gap-1
          bg-slate-900/95 border-r border-cyan-500/10 backdrop-blur-[12px] overflow-y-auto z-[100]
          hidden md:flex
          md:sticky md:top-0 md:h-screen
          md:translate-x-0
          fixed inset-y-0 left-0 transform transition-transform duration-200 ease-out
          ${isOpen ? "translate-x-0 flex" : "-translate-x-full"}
        `}
        style={{ gap: 4 }}
      >
        {/* Logo */}
        <div className="pb-6 px-1 border-b border-cyan-500/10">
          <div className="flex items-center gap-2">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
              style={{
                background: "var(--accent-dim)",
                border: "1px solid var(--accent)",
                boxShadow: "0 0 12px rgba(0,212,255,0.2)",
              }}
            >
              <Zap size={16} color="var(--accent)" />
            </div>
            <div>
              <div className="neon-text text-[0.82rem] font-bold leading-tight">
                Stocks With Gaurav
              </div>
              <div
                className="text-[0.62rem] tracking-wide"
                style={{ color: "var(--text-secondary)" }}
              >
                SMC DASHBOARD
              </div>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex flex-col gap-0.5 mt-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = path === href || (href !== "/" && path.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                onClick={onClose}
                className={`nav-link ${active ? "active" : ""}`}
              >
                <Icon size={16} />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* AI Bot Widget */}
        <div className="mt-auto pt-4">
          <SidebarBotWidget />
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-cyan-500/10">
          <div
            className="text-[0.7rem] text-center"
            style={{ color: "var(--text-dim)" }}
          >
            AI Engine · Active
          </div>
        </div>
      </aside>
    </>
  );
}
