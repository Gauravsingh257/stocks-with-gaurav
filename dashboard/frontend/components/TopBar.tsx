"use client";
import { useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useHealth } from "@/lib/useHealth";
import { useAuth } from "@/lib/auth";
import { Wifi, WifiOff, RefreshCw, Database, Activity, Sun, Moon, SlidersHorizontal } from "lucide-react";
import { useTheme } from "@/components/ThemeProvider";

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

interface TopBarProps {
  onMenuClick?: () => void;
  terminalLayout?: boolean;
  onTerminalLayoutToggle?: () => void;
}

export default function TopBar({ onMenuClick, terminalLayout = false, onTerminalLayoutToggle }: TopBarProps) {
  const { theme, toggle: toggleTheme } = useTheme();
  const { snapshot, status, globalStateVersion } = useEngineSocket();
  const health = useHealth();
  const { user } = useAuth();
  const isAdmin = !!user && ADMIN_EMAILS.has((user.email || "").trim().toLowerCase());
  // Mobile diagnostics dropdown (info-hierarchy: keep only high-value trading
  // info inline on small screens; tuck diagnostics behind this control).
  const [showDiag, setShowDiag] = useState(false);

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
        Market regime {rb.label}. {sigToday} of {maxSig} signals today.
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

      {/* Regime */}
      <span className={`${rb.cls} shrink-0`}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: rb.dot, display: "inline-block" }} />
        {rb.label}
      </span>

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
      </div>
      </div>
    </header>
  );
}
