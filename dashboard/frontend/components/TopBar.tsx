"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type MarketStateResponse } from "@/lib/api";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useHealth } from "@/lib/useHealth";
import { useAuth } from "@/lib/auth";
import { Wifi, WifiOff, RefreshCw, Database, Activity, Sun, Moon, SlidersHorizontal, Search, LogIn, LogOut, Crown } from "lucide-react";
import { useTheme } from "@/components/ThemeProvider";
import { openGlobalSearch } from "@/components/CommandPalette";

// Admin-only operator controls (Connect Kite, etc.) are gated by exact
// email match. Anyone else logged in or anonymous never sees them.
const ADMIN_EMAILS = new Set<string>([
  "hellogaurav2577@gmail.com",
]);

function regimeBadge(r: string) {
  if (r === "BULLISH") return { cls: "badge badge-win", dot: "var(--success)", label: "BULLISH" };
  if (r === "BEARISH") return { cls: "badge badge-loss", dot: "var(--danger)", label: "BEARISH" };
  return { cls: "badge badge-neutral", dot: "var(--muted)", label: "NEUTRAL" };
}

// Market Health = the single authoritative market-regime model (same one the
// Research page's banner uses). The top-bar chip surfaces it so the whole site
// shows ONE consistent regime label — no more "BULLISH tape" vs "Defensive" clash.
const MH_LABEL: Record<string, string> = {
  STRONG_BULL: "Strong Bull", WEAK_BULL: "Weak Bull", SIDEWAYS: "Sideways",
  CORRECTION: "Correction", BEAR: "Bear", UNKNOWN: "Neutral",
};
function mhTone(exposureLabel?: string): string {
  const l = exposureLabel || "";
  if (l.includes("Aggressive") || l.includes("Normal")) return "var(--success)";
  if (l.includes("Defensive")) return "var(--warning)";
  if (l.includes("Risk-Off")) return "var(--danger)";
  return "var(--muted)";
}

interface TopBarProps {
  onMenuClick?: () => void;
  terminalLayout?: boolean;
  onTerminalLayoutToggle?: () => void;
}

export default function TopBar({ onMenuClick, terminalLayout = false, onTerminalLayoutToggle }: TopBarProps) {
  const { theme, toggle: toggleTheme } = useTheme();
  const { snapshot, status, globalStateVersion } = useEngineSocket();
  const health = useHealth();
  const { user, logout } = useAuth();
  const isAdmin = !!user && ADMIN_EMAILS.has((user.email || "").trim().toLowerCase());
  // Mobile diagnostics dropdown (info-hierarchy: keep only high-value trading
  // info inline on small screens; tuck diagnostics behind this control).
  const [showDiag, setShowDiag] = useState(false);
  const [showProfile, setShowProfile] = useState(false);

  // Single source of truth for the market-regime label (Market Health model).
  const [mktState, setMktState] = useState<MarketStateResponse | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.marketState().then((s) => alive && setMktState(s)).catch(() => {});
    load();
    const id = setInterval(load, 5 * 60 * 1000);  // refresh every 5 min
    return () => { alive = false; clearInterval(id); };
  }, []);
  const mhLabel = mktState ? (MH_LABEL[mktState.market_state] ?? mktState.market_state) : null;

  const regime = snapshot?.market_regime ?? "NEUTRAL";
  const rb = regimeBadge(regime);
  const pnlR = snapshot?.daily_pnl_r ?? 0;
  const cb   = snapshot?.circuit_breaker_active ?? false;
  const paper = snapshot?.paper_mode ?? false;
  const sigToday = snapshot?.signals_today ?? 0;
  const maxSig   = snapshot?.max_daily_signals ?? 5;
  const hasData = snapshot != null;
  const transportLabel =
    status === "connected" ? "WS LIVE" : status === "polling" ? "POLLING" : hasData ? "RECONNECTING" : "STANDBY";
  const transportColor =
    status === "connected" ? "var(--success)" : status === "polling" ? "var(--accent)" : "var(--text-dim)";

  // ── Diagnostic nodes (rendered inline on desktop, in the mobile dropdown) ──
  const diagTimestamp = snapshot?.snapshot_time ? (
    <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--text-dim)", fontSize: "0.7rem" }}>
      <RefreshCw size={11} />
      {new Date(snapshot.snapshot_time).toLocaleTimeString()}
    </div>
  ) : null;

  const diagLayoutToggle = onTerminalLayoutToggle ? (
    <button
      type="button"
      onClick={onTerminalLayoutToggle}
      className="shrink-0 px-3 py-2 lg:py-1 rounded text-xs font-medium border border-cyan-500/30 hover:border-cyan-500/50 text-slate-300 hover:text-cyan-300 transition-colors text-left"
    >
      {terminalLayout ? "Classic Layout" : "Terminal Layout"}
    </button>
  ) : null;

  // Connect / Refresh Kite — ADMIN-ONLY (exact email match in ADMIN_EMAILS).
  const diagKite = isAdmin && health ? (() => {
    const healthy = health.kite_connected === true && health.token_present === true;
    return (
      <button
        type="button"
        onClick={() => { window.location.href = "/api/kite/login"; }}
        title={healthy ? "Kite session active — click to refresh manually" : "Kite disconnected — click to connect"}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "8px 12px", fontSize: "0.75rem", fontWeight: 600,
          color: healthy ? "var(--text-secondary)" : "var(--accent)",
          background: "transparent",
          border: `1px solid ${healthy ? "var(--border)" : "var(--accent)"}`,
          borderRadius: 6, cursor: "pointer", opacity: healthy ? 0.65 : 1,
        }}
      >
        <Wifi size={12} />
        {healthy ? "Refresh Kite" : "Connect Kite"}
      </button>
    );
  })() : null;

  const diagHealth = health ? (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.65rem", color: "var(--text-secondary)" }}>
      <span title={health.db_connected ? "DB connected" : "DB error"}>
        <Database size={11} color={health.db_connected ? "var(--success)" : "var(--danger)"} />
      </span>
      <span title={`${health.ws_clients} WebSocket client(s)`}>
        <Activity size={11} color={(health.ws_clients ?? 0) > 0 ? "var(--success)" : "var(--warning)"} />
      </span>
      {typeof health.token_expires_in_hours === "number" && (
        <span title="Kite token TTL (hours)">Kite {health.token_expires_in_hours}h</span>
      )}
      <span style={{ color: "var(--text-dim)" }}>v{health.backend_version}</span>
    </div>
  ) : null;

  return (
    <header
      className="h-14 sticky top-0 z-50 flex items-center px-4 md:px-6 gap-2 md:gap-5 shrink-0 overflow-x-hidden"
      style={{
        background: "rgba(15,23,42,0.9)",
        borderBottom: "1px solid rgba(6,182,212,0.2)",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Scoped screen-reader live region: announces the MEANINGFUL market
          state (regime + signal count) on change. P&L is intentionally excluded
          — it ticks too often and would spam assistive tech. */}
      <div
        aria-live="polite"
        aria-atomic="true"
        style={{ position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0,0,0,0)", whiteSpace: "nowrap", border: 0 }}
      >
        Market {mhLabel ?? rb.label}. {sigToday} of {maxSig} signals today.
      </div>

      {/* Hamburger - mobile only */}
      <button
        type="button"
        className="tap-44 md:hidden p-2 rounded-md -ml-1 text-[var(--text-primary)] hover:bg-white/5"
        onClick={onMenuClick}
        aria-label="Open menu"
      >
        &#9776;
      </button>

      {/* WS transport status */}
      <div className="flex items-center gap-1.5 shrink-0">
        {status === "connected" || status === "polling"
          ? <Wifi size={14} color={status === "connected" ? "var(--success)" : "var(--accent)"} />
          : <WifiOff size={14} color="var(--text-dim)" />
        }
        <span style={{ fontSize: "0.72rem", color: transportColor }}>
          {transportLabel}
          {globalStateVersion > 0 ? <span className="hidden lg:inline"> · v{globalStateVersion}</span> : null}
        </span>
      </div>

      <div className="w-px h-5 bg-[var(--border)] shrink-0" />

      {/* Badges row — engine heartbeat lives in LiveEngineRibbon */}
      <div className="flex items-center gap-2 md:gap-5 overflow-x-auto min-w-0 flex-1">
      {/* Engine mode */}
      <span className={`badge shrink-0 ${paper ? "badge-paper" : "badge-live"}`}>
        <span className="pulse-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: paper ? "var(--warning)" : "var(--success)", display: "inline-block" }} />
        {paper ? "PAPER" : "LIVE"}<span className="hidden sm:inline"> · {snapshot?.engine_mode ?? "—"}</span>
      </span>

      {/* Market regime — Market Health model (matches the Research banner).
          Falls back to the engine tape regime only if Market Health is unavailable. */}
      {mktState ? (
        <span
          className="badge shrink-0"
          title={`Market Health ${Math.round(mktState.market_health ?? 0)}/100`
            + (mktState.opportunity_level ? ` · ${mktState.opportunity_level}` : "")
            + (mktState.exposure_label ? ` · ${mktState.exposure_label}` : "")}
          style={{ color: mhTone(mktState.exposure_label), whiteSpace: "nowrap" }}
        >
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: mhTone(mktState.exposure_label), display: "inline-block" }} />
          {mhLabel}{typeof mktState.market_health === "number" ? ` · ${Math.round(mktState.market_health)}` : ""}
        </span>
      ) : (
        <span className={`${rb.cls} shrink-0`}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: rb.dot, display: "inline-block" }} />
          {rb.label}
        </span>
      )}

      {/* Circuit breaker */}
      {cb && (
        <span className="badge badge-halt shrink-0">
          ⛔ CIRCUIT BREAKER
        </span>
      )}

      <div className="ml-auto flex items-center gap-3 md:gap-4 shrink-0">
        {/* Daily PnL — high-value, always visible */}
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: "0.6rem", color: "var(--text-secondary)", letterSpacing: "0.05em" }}>DAILY PnL</div>
          <div style={{
            fontSize: "clamp(0.8rem, 3vw, 0.9rem)", fontWeight: 700,
            color: pnlR >= 0 ? "var(--success)" : "var(--danger)",
          }}>
            {pnlR >= 0 ? "+" : ""}{pnlR.toFixed(2)}R
          </div>
        </div>

        {/* Signals — high-value, always visible */}
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: "0.6rem", color: "var(--text-secondary)", letterSpacing: "0.05em" }}>SIGNALS</div>
          <div style={{ fontSize: "clamp(0.8rem, 3vw, 0.9rem)", fontWeight: 700, color: "var(--text-primary)" }}>
            {sigToday}/{maxSig}
          </div>
        </div>

        {/* Global search — always visible, all breakpoints. Opens the command
            palette (stock symbol → /stock/[symbol], or any page). */}
        <button
          type="button"
          onClick={openGlobalSearch}
          title="Search stocks & pages (Ctrl/⌘+K)"
          aria-label="Search"
          className="grid place-items-center w-11 h-11 lg:w-8 lg:h-8 rounded-md shrink-0"
          style={{
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.08)",
            cursor: "pointer", color: "var(--text-secondary)",
          }}
        >
          <Search size={16} />
        </button>

        {/* Theme toggle — always visible (≥44px touch target on mobile) */}
        <button
          type="button"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="grid place-items-center w-11 h-11 lg:w-8 lg:h-8 rounded-md shrink-0"
          style={{
            background: "rgba(255,255,255,0.05)",
            border: "1px solid rgba(255,255,255,0.08)",
            cursor: "pointer", color: "var(--text-secondary)",
            transition: "background 0.2s",
          }}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {/* ── Diagnostics — inline on desktop (≥lg), unchanged ──────────── */}
        <div className="hidden lg:flex items-center gap-4">
          {diagTimestamp}
          {diagLayoutToggle}
          {diagKite}
          {diagHealth}
        </div>

        {/* ── Diagnostics — behind a settings control on mobile (<lg) ───── */}
        <div className="lg:hidden relative shrink-0">
          <button
            type="button"
            onClick={() => setShowDiag((v) => !v)}
            aria-label="System diagnostics"
            aria-expanded={showDiag}
            className="grid place-items-center w-11 h-11 rounded-md"
            style={{
              background: showDiag ? "rgba(6,182,212,0.15)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${showDiag ? "var(--accent)" : "rgba(255,255,255,0.08)"}`,
              color: "var(--text-secondary)", cursor: "pointer",
            }}
          >
            <SlidersHorizontal size={16} />
          </button>
          {showDiag && (
            <>
              {/* click-away backdrop */}
              <div className="fixed inset-0 z-40" onClick={() => setShowDiag(false)} aria-hidden />
              <div
                className="absolute right-0 top-full mt-2 z-50 flex flex-col gap-3 rounded-lg p-3 min-w-[220px]"
                style={{
                  background: "rgba(15,23,42,0.98)",
                  border: "1px solid rgba(6,182,212,0.25)",
                  boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
                  backdropFilter: "blur(12px)",
                }}
              >
                <div style={{ fontSize: "0.55rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                  Diagnostics
                </div>
                {diagTimestamp}
                {diagLayoutToggle}
                {diagKite}
                {diagHealth}
              </div>
            </>
          )}
        </div>

        {/* ── Auth — Login / Profile, always visible at every breakpoint.
              Fixes the launch blocker where sign-in was reachable only from
              the bottom of the mobile drawer. ─────────────────────────────── */}
        {user ? (
          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => setShowProfile((v) => !v)}
              aria-label="Account menu"
              aria-expanded={showProfile}
              className="grid place-items-center w-11 h-11 lg:w-8 lg:h-8 rounded-full shrink-0"
              style={{
                background: "var(--accent-dim)", border: "1px solid var(--accent)",
                color: "var(--accent)", fontSize: "0.8rem", fontWeight: 700, cursor: "pointer",
              }}
            >
              {(user.name?.[0] || user.email[0] || "?").toUpperCase()}
            </button>
            {showProfile && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowProfile(false)} aria-hidden />
                <div
                  className="absolute right-0 top-full mt-2 z-50 flex flex-col gap-1 rounded-lg p-2 min-w-[200px]"
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
                  <button
                    onClick={() => { setShowProfile(false); logout(); }}
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
            className="inline-flex items-center gap-1.5 shrink-0 rounded-md font-semibold"
            style={{
              padding: "8px 12px", fontSize: "0.78rem",
              background: "var(--accent-dim)", border: "1px solid var(--accent)", color: "var(--accent)",
              textDecoration: "none",
            }}
          >
            <LogIn size={14} /> <span className="hidden sm:inline">Sign In</span>
          </Link>
        )}
      </div>
      </div>
    </header>
  );
}
