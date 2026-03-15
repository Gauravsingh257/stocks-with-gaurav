"use client";
import { useEffect, useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { Wifi, WifiOff, RefreshCw, Database, Activity } from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "";

function regimeBadge(r: string) {
  if (r === "BULLISH") return { cls: "badge badge-win", dot: "var(--success)", label: "BULLISH" };
  if (r === "BEARISH") return { cls: "badge badge-loss", dot: "var(--danger)", label: "BEARISH" };
  return { cls: "badge badge-neutral", dot: "var(--muted)", label: "NEUTRAL" };
}

interface HealthData {
  db_connected: boolean;
  ws_clients: number;
  engine_live: boolean;
  backend_version: string;
  engine_version: string;
  uptime_human: string;
  kite_connected?: boolean;
  token_present?: boolean;
  token_expires_in_hours?: number | null;
}

interface TopBarProps {
  onMenuClick?: () => void;
  terminalLayout?: boolean;
  onTerminalLayoutToggle?: () => void;
}

export default function TopBar({ onMenuClick, terminalLayout = false, onTerminalLayoutToggle }: TopBarProps) {
  const { snapshot, status } = useEngineSocket();
  const [health, setHealth] = useState<HealthData | null>(null);

  useEffect(() => {
    const fetchHealth = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      fetch(`${BASE}/api/system/health`)
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setHealth(d))
        .catch(() => {});
    };
    fetchHealth();
    const t = setInterval(fetchHealth, 30_000); // was 15s
    return () => clearInterval(t);
  }, []);

  const regime = snapshot?.market_regime ?? "NEUTRAL";
  const rb = regimeBadge(regime);
  const pnlR = snapshot?.daily_pnl_r ?? 0;
  const cb   = snapshot?.circuit_breaker_active ?? false;
  const paper = snapshot?.paper_mode ?? false;
  const sigToday = snapshot?.signals_today ?? 0;
  const maxSig   = snapshot?.max_daily_signals ?? 5;
  const engineRunning = snapshot?.engine_running ?? false;
  const hbAge = snapshot?.engine_heartbeat_age_sec;

  return (
    <header
      className="h-14 sticky top-0 z-50 flex items-center px-4 md:px-6 gap-2 md:gap-5 shrink-0 overflow-x-hidden"
      style={{
        background: "rgba(15,23,42,0.9)",
        borderBottom: "1px solid rgba(6,182,212,0.2)",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Hamburger - mobile only */}
      <button
        type="button"
        className="md:hidden p-2 rounded-md -ml-1 text-[var(--text-primary)] hover:bg-white/5"
        onClick={onMenuClick}
        aria-label="Open menu"
      >
        &#9776;
      </button>

      {/* WS transport status */}
      <div className="flex items-center gap-1.5 shrink-0">
        {status === "connected" || status === "polling"
          ? <Wifi size={14} color={status === "connected" ? "var(--success)" : "var(--accent)"} />
          : <WifiOff size={14} color="var(--danger)" />
        }
        <span style={{ fontSize: "0.72rem", color: status === "connected" ? "var(--success)" : status === "polling" ? "var(--accent)" : "var(--danger)" }}>
          {status === "connected" ? "WS LIVE" : status === "polling" ? "POLLING" : status.toUpperCase()}
        </span>
      </div>

      <div className="w-px h-5 bg-[var(--border)] shrink-0" />

      {/* Badges row - scroll on small screens */}
      <div className="flex items-center gap-2 md:gap-5 overflow-x-auto min-w-0 flex-1">
      {/* Engine loop heartbeat status */}
      <span
        className={`badge shrink-0 ${engineRunning ? "badge-live" : "badge-loss"}`}
        title={!snapshot?.engine_live && !engineRunning ? "Backend runs separately from engine — STALE is normal. Charts work if Kite is set on web." : undefined}
      >
        <span
          className="pulse-dot"
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: engineRunning ? "var(--success)" : "var(--danger)",
            display: "inline-block",
          }}
        />
        ENGINE {engineRunning ? "RUNNING" : "STALE"}
        {typeof hbAge === "number" ? ` · ${hbAge.toFixed(0)}s` : ""}
      </span>

      {/* Engine mode */}
      <span className={`badge shrink-0 ${paper ? "badge-paper" : "badge-live"}`}>
        <span className="pulse-dot" style={{ width: 6, height: 6, borderRadius: "50%", background: paper ? "var(--warning)" : "var(--success)", display: "inline-block" }} />
        {paper ? "PAPER" : "LIVE"} · {snapshot?.engine_mode ?? "—"}
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

      <div className="ml-auto flex items-center gap-2 md:gap-5 shrink-0">
        {/* Daily PnL */}
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", letterSpacing: "0.05em" }}>DAILY PnL</div>
          <div style={{
            fontSize: "0.9rem", fontWeight: 700,
            color: pnlR >= 0 ? "var(--success)" : "var(--danger)",
          }}>
            {pnlR >= 0 ? "+" : ""}{pnlR.toFixed(2)}R
          </div>
        </div>

        {/* Signals */}
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", letterSpacing: "0.05em" }}>SIGNALS</div>
          <div style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--text-primary)" }}>
            {sigToday}/{maxSig}
          </div>
        </div>

        {/* Timestamp */}
        {snapshot?.snapshot_time && (
          <div style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--text-dim)", fontSize: "0.7rem" }}>
            <RefreshCw size={11} />
            {new Date(snapshot.snapshot_time).toLocaleTimeString()}
          </div>
        )}

        {/* Layout toggle — Terminal (multi-panel) vs Classic (single page) */}
        {onTerminalLayoutToggle && (
          <button
            type="button"
            onClick={onTerminalLayoutToggle}
            className="shrink-0 px-2 py-1 rounded text-xs font-medium border border-cyan-500/30 hover:border-cyan-500/50 text-slate-300 hover:text-cyan-300 transition-colors"
          >
            {terminalLayout ? "Classic Layout" : "Terminal Layout"}
          </button>
        )}

        {/* Connect Kite — show when Kite disconnected or token missing; uses /api/kite/login proxy */}
        {health && (health.kite_connected === false || health.token_present === false) && (
          <button
            type="button"
            onClick={() => { window.location.href = "/api/kite/login"; }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 12px",
              fontSize: "0.75rem",
              fontWeight: 600,
              color: "var(--accent)",
              background: "transparent",
              border: "1px solid var(--accent)",
              borderRadius: 6,
              cursor: "pointer",
            }}
          >
            <Wifi size={12} />
            Connect Kite
          </button>
        )}

        {/* System health dots */}
        {health && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.65rem", color: "var(--text-secondary)" }}>
            <span title={health.db_connected ? "DB connected" : "DB error"}>
              <Database size={11} color={health.db_connected ? "var(--success)" : "var(--danger)"} />
            </span>
            <span title={`${health.ws_clients} WebSocket client(s)`}>
              <Activity size={11} color={health.ws_clients > 0 ? "var(--success)" : "var(--warning)"} />
            </span>
            {typeof health.token_expires_in_hours === "number" && (
              <span title="Kite token TTL (hours)">
                Kite {health.token_expires_in_hours}h
              </span>
            )}
            <span style={{ color: "var(--text-dim)" }}>
              v{health.backend_version}
            </span>
          </div>
        )}
      </div>
      </div>
    </header>
  );
}
