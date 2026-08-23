"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart2, Bot, Globe, Zap, Bookmark, LogIn, LogOut, Crown, Sparkles, LayoutDashboard, Home, Activity
} from "lucide-react";
import { SidebarBotWidget } from "@/components/FuturisticElements";
import { useAuth } from "@/lib/auth";

// Default ON (live); set NEXT_PUBLIC_PIL_ENABLED=0 to hide.
const PIL_ENABLED = process.env.NEXT_PUBLIC_PIL_ENABLED !== "0";
const ADMIN_EMAILS = new Set(["hellogaurav2577@gmail.com"]);

// Nav consolidated 8 flat items → 5 grouped destinations. Folded pages remain
// one click away via <SectionTabs> inside each group:
//   Research  ← Screeners, Stock Universe
//   Portfolio ← Track Record (/analytics), Journal
//   Markets   ← OI Radar, Market Intel
type NavItem = { href: string; label: string; icon: typeof BarChart2; auth?: boolean; admin?: boolean; match?: string[] };
const NAV: NavItem[] = [
  { href: "/command",         label: "Command Center", icon: Home },
  { href: "/terminal",        label: "Terminal",  icon: Sparkles },
  { href: "/research",        label: "Research",  icon: Bot,      match: ["/research", "/screeners", "/universe"] },
  { href: "/watchlist",       label: "Watchlist", icon: Bookmark, auth: true },
  ...(PIL_ENABLED
    ? [{ href: "/intelligence", label: "Portfolio", icon: LayoutDashboard, auth: true, match: ["/intelligence", "/analytics", "/journal"] }]
    : [{ href: "/analytics",    label: "Portfolio", icon: LayoutDashboard, match: ["/analytics", "/journal"] }]),
  { href: "/oi-intelligence", label: "Markets",   icon: Globe, match: ["/oi-intelligence", "/market-intelligence"] },
  { href: "/health",          label: "Product Health", icon: Activity, admin: true },
];

export default function Sidebar({
  isOpen = false,
  onClose,
}: {
  isOpen?: boolean;
  onClose?: () => void;
}) {
  const path = usePathname();
  const { user, logout } = useAuth();
  const isAdmin = !!user && (user.role === "ADMIN" || ADMIN_EMAILS.has((user.email || "").toLowerCase()));

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
          {NAV.map(({ href, label, icon: Icon, auth, admin, match }) => {
            if (auth && !user) return null;
            if (admin && !isAdmin) return null;
            const prefixes = match ?? [href];
            const active = prefixes.some((p) => path === p || (p !== "/" && path.startsWith(p)));
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

        {/* Auth section */}
        <div className="pt-3 border-t border-cyan-500/10">
          {user ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px" }}>
                <div style={{ width: 28, height: 28, borderRadius: "50%", background: "var(--accent-dim)", border: "1px solid var(--accent)", display: "grid", placeItems: "center", fontSize: "0.7rem", fontWeight: 700, color: "var(--accent)" }}>
                  {user.name?.[0]?.toUpperCase() || user.email[0].toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {user.name || user.email}
                  </div>
                  <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 3 }}>
                    {user.role === "PREMIUM" && <Crown size={9} color="#f59e0b" />}
                    {user.role}
                  </div>
                </div>
                <button onClick={logout} title="Sign out" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)", padding: 4 }}>
                  <LogOut size={14} />
                </button>
              </div>
            </div>
          ) : (
            <Link href="/login" onClick={onClose} className="nav-link" style={{ justifyContent: "center", fontSize: "0.8rem" }}>
              <LogIn size={14} /> Sign In
            </Link>
          )}
          {!user && (
            <div className="text-[0.65rem] text-center mt-2" style={{ color: "var(--text-dim)" }}>
              Sign in to unlock your personal Watchlist
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
