"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Zap, LogIn, LogOut, Crown, ChevronDown } from "lucide-react";
import { SidebarBotWidget } from "@/components/FuturisticElements";
import { useAuth } from "@/lib/auth";
import { NAV, activeSectionHref, type NavChild } from "@/lib/navGroups";

const ADMIN_EMAILS = new Set(["hellogaurav2577@gmail.com"]);
const OPEN_KEY = "swg-nav-open";

const isActive = (path: string, href: string, extra?: string[]) => {
  const prefixes = [href, ...(extra ?? [])];
  return prefixes.some((p) => path === p || (p !== "/" && path.startsWith(p)));
};

export default function Sidebar({
  isOpen = false,
  onClose,
}: {
  isOpen?: boolean;
  onClose?: () => void;
}) {
  const path = usePathname() || "";
  const { user, logout } = useAuth();
  const isAdmin = !!user && (user.role === "ADMIN" || ADMIN_EMAILS.has((user.email || "").toLowerCase()));

  // Which sections are expanded. Seeded with the active section so the current
  // page's siblings are visible on first paint (same on server and client — no
  // hydration mismatch); persisted expansions are merged in after mount.
  const activeHref = activeSectionHref(path);
  const [open, setOpen] = useState<Set<string>>(() => new Set(activeHref ? [activeHref] : []));
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(OPEN_KEY);
      const stored: string[] = raw ? JSON.parse(raw) : [];
      if (stored.length) {
        setOpen((prev) => {
          const next = new Set(prev);
          stored.forEach((h) => next.add(h));
          return next;
        });
      }
    } catch {
      /* private mode / blocked storage — fall back to active-only */
    }
    setHydrated(true);
  }, []);

  // Keep the section containing the current page open as the route changes.
  useEffect(() => {
    if (!activeHref) return;
    setOpen((prev) => (prev.has(activeHref) ? prev : new Set(prev).add(activeHref)));
  }, [activeHref]);

  // Remember expanded sections for the rest of the session.
  useEffect(() => {
    if (!hydrated) return;
    try {
      sessionStorage.setItem(OPEN_KEY, JSON.stringify([...open]));
    } catch {
      /* ignore */
    }
  }, [open, hydrated]);

  const toggle = (href: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(href)) next.delete(href);
      else next.add(href);
      return next;
    });

  const childIsActive = (c: NavChild) => isActive(path, c.href, c.match);

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

        {/* Nav — the single source of truth for app navigation */}
        <nav className="flex flex-col gap-0.5 mt-2" aria-label="Primary">
          {NAV.map((s) => {
            if (s.auth && !user) return null;
            if (s.admin && !isAdmin) return null;

            const sectionActive = isActive(path, s.href, s.match);
            const Icon = s.icon;

            // Leaf section — a plain link (Home, Terminal, Watchlist, Product Health)
            if (!s.children?.length) {
              return (
                <Link
                  key={s.href}
                  href={s.href}
                  onClick={onClose}
                  className={`nav-link ${sectionActive ? "active" : ""}`}
                  aria-current={sectionActive ? "page" : undefined}
                >
                  <Icon size={16} />
                  {s.label}
                </Link>
              );
            }

            // Expandable section — label navigates, chevron toggles
            const expanded = open.has(s.href);
            const panelId = `nav-${s.href.replace(/\W+/g, "-")}`;
            return (
              <div key={s.href} className="nav-section">
                <div className={`nav-parent ${sectionActive ? "active" : ""}`}>
                  <Link href={s.href} onClick={onClose} className="nav-parent-link">
                    <Icon size={16} />
                    {s.label}
                  </Link>
                  <button
                    type="button"
                    className="nav-chevron"
                    aria-label={`${expanded ? "Collapse" : "Expand"} ${s.label}`}
                    aria-expanded={expanded}
                    aria-controls={panelId}
                    onClick={() => toggle(s.href)}
                  >
                    <ChevronDown
                      size={14}
                      style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}
                    />
                  </button>
                </div>
                <div id={panelId} className="nav-children" hidden={!expanded}>
                  {s.children.map((c) => {
                    const active = childIsActive(c);
                    return (
                      <Link
                        key={c.href}
                        href={c.href}
                        onClick={onClose}
                        className={`nav-child ${active ? "active" : ""}`}
                        aria-current={active ? "page" : undefined}
                      >
                        {c.label}
                      </Link>
                    );
                  })}
                </div>
              </div>
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
