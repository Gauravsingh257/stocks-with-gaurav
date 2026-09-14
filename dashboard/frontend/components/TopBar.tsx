"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Sun, Moon, LogIn, LogOut, Crown } from "lucide-react";
import { useTheme } from "@/components/ThemeProvider";
import SearchPill from "@/components/SearchPill";
import { OperatorAttentionDot, OperatorMenuSection } from "@/components/OperatorStatus";

// Operator controls (Kite login, engine and system status) are gated by exact
// email match and live in the account menu. Anyone else never sees them.
const ADMIN_EMAILS = new Set<string>([
  "hellogaurav2577@gmail.com",
]);

interface TopBarProps {
  onMenuClick?: () => void;
}

/**
 * Global header — deliberately minimal: navigation, search, theme, account.
 *
 * Until 2026-09-15 it also carried engine telemetry for every visitor: WS transport
 * and state version, engine mode, the Market Health chip, the engine's own daily R
 * and signal count, snapshot time, a "Terminal Layout" switch, Refresh Kite, Kite
 * TTL and backend version. None of it was about the visitor, and each item already
 * has a better home:
 *   - connection, freshness, engine and Kite health → MarketCommandBar's status dot
 *     and BackendStatusNotice
 *   - market regime, today's signals, "your day" R → the Command Center page
 *   - operator controls and diagnostics → the admin-only Operator section of the
 *     account menu (components/OperatorStatus)
 * Dropping the socket and the Market Health poll from here also means one less
 * WebSocket and one less periodic API call per open tab.
 */
export default function TopBar({ onMenuClick }: TopBarProps) {
  const { theme, toggle: toggleTheme } = useTheme();
  const { user, logout } = useAuth();
  const isAdmin = !!user && ADMIN_EMAILS.has((user.email || "").trim().toLowerCase());
  const [showProfile, setShowProfile] = useState(false);
  const closeProfile = () => setShowProfile(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the account menu on an outside press or Escape. (A full-screen backdrop
  // div cannot do this here: the header's backdrop-filter makes it the containing
  // block for `position: fixed`, so the backdrop only ever covered the 56px bar.)
  useEffect(() => {
    if (!showProfile) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowProfile(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowProfile(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [showProfile]);

  return (
    <header
      className="h-14 sticky top-0 z-50 flex items-center gap-3 px-4 md:px-6 shrink-0"
      style={{
        background: "rgba(15,23,42,0.9)",
        borderBottom: "1px solid rgba(6,182,212,0.2)",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Hamburger — mobile only */}
      <button
        type="button"
        className="tap-44 md:hidden p-2 rounded-md -ml-1 text-[var(--text-primary)] hover:bg-white/5"
        onClick={onMenuClick}
        aria-label="Open menu"
      >
        &#9776;
      </button>

      {/* Search — the header's primary action. A wide pill on md+ … */}
      <div className="hidden md:flex flex-1 min-w-0">
        <SearchPill variant="pill" />
      </div>
      <div className="flex-1 md:hidden" aria-hidden />

      <div className="flex items-center gap-2 shrink-0">
        {/* … and a ≥44px icon beside the other actions on small screens. */}
        <div className="md:hidden">
          <SearchPill variant="icon" />
        </div>

        {/* Theme toggle (≥44px touch target below lg) */}
        <button
          type="button"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="grid place-items-center w-11 h-11 lg:w-9 lg:h-9 rounded-md shrink-0"
          style={{
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.08)",
            cursor: "pointer", color: "var(--text-secondary)",
            transition: "background 0.2s",
          }}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {/* Account — Login / Profile at every breakpoint. */}
        {user ? (
          <div ref={menuRef} className="relative shrink-0">
            <button
              type="button"
              onClick={() => setShowProfile((v) => !v)}
              aria-label="Account menu"
              aria-expanded={showProfile}
              className="relative grid place-items-center w-11 h-11 lg:w-9 lg:h-9 rounded-full shrink-0"
              style={{
                background: "var(--accent-dim)", border: "1px solid var(--accent)",
                color: "var(--accent)", fontSize: "0.8rem", fontWeight: 700, cursor: "pointer",
              }}
            >
              {(user.name?.[0] || user.email[0] || "?").toUpperCase()}
              {isAdmin && <OperatorAttentionDot />}
            </button>
            {showProfile && (
              <>
                <div
                  className="absolute right-0 top-full mt-2 z-50 flex flex-col gap-1 rounded-lg p-1.5 min-w-[240px]"
                  style={{
                    background: "rgba(15,23,42,0.98)", border: "1px solid rgba(6,182,212,0.25)",
                    boxShadow: "0 12px 32px rgba(0,0,0,0.5)", backdropFilter: "blur(12px)",
                  }}
                >
                  <div style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)" }}>
                    <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {user.name || user.email}
                    </div>
                    <div style={{ fontSize: "0.66rem", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 4, marginTop: 2 }}>
                      {user.role === "PREMIUM" && <Crown size={10} color="#f59e0b" />}
                      {user.role}
                    </div>
                  </div>
                  {isAdmin && <OperatorMenuSection onNavigate={closeProfile} />}
                  <button
                    onClick={() => { closeProfile(); logout(); }}
                    className="flex items-center gap-2 rounded-md text-left"
                    style={{ padding: "8px 10px", fontSize: "0.8rem", color: "var(--text-secondary)", background: "none", border: "none", cursor: "pointer" }}
                  >
                    <LogOut size={14} /> Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        ) : (
          <Link
            href="/login"
            className="inline-flex items-center justify-center gap-1.5 shrink-0 rounded-md font-semibold h-11 lg:h-9 min-w-[44px] px-3"
            style={{
              fontSize: "0.78rem",
              background: "var(--accent-dim)", border: "1px solid var(--accent)", color: "var(--accent)",
              textDecoration: "none",
            }}
          >
            <LogIn size={14} /> <span className="hidden sm:inline">Sign In</span>
          </Link>
        )}
      </div>
    </header>
  );
}
