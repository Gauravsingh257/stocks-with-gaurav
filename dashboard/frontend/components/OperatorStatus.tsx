"use client";

import Link from "next/link";
import { Activity, Database, Wifi } from "lucide-react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useHealth, type HealthData } from "@/lib/useHealth";

/**
 * Operator controls and system status — ADMIN ONLY, shown inside the account menu.
 *
 * These used to sit in the global header for every visitor (WS transport and state
 * version, engine mode, Kite TTL, backend version, Refresh Kite), where they read as
 * developer clutter. They are kept here for the operator. The section mounts only
 * when an admin opens the menu, so the engine WebSocket it uses is never opened for
 * ordinary visitors.
 */

/** The Kite session needs the operator (no token, or not connected). */
export function kiteNeedsAttention(health: HealthData | null): boolean {
  if (!health) return false;
  return health.token_present === false || health.kite_connected === false;
}

/**
 * A small dot on the avatar when Kite needs a login. Moving "Connect Kite" out of
 * the header must never be able to hide an outage from the operator.
 */
export function OperatorAttentionDot() {
  const health = useHealth();
  if (!kiteNeedsAttention(health)) return null;
  return (
    <span
      aria-hidden
      title="Kite needs a login"
      className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full"
      style={{ background: "var(--warning)", boxShadow: "0 0 0 2px rgba(15,23,42,0.95)" }}
    />
  );
}

const LABEL: React.CSSProperties = {
  fontSize: "0.58rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.1em",
};
const KEY: React.CSSProperties = { fontSize: "0.72rem", color: "var(--text-dim)" };
const VALUE: React.CSSProperties = { fontSize: "0.72rem", fontWeight: 600, textAlign: "right" };

export function OperatorMenuSection({ onNavigate }: { onNavigate?: () => void }) {
  const { snapshot, status } = useEngineSocket();
  const health = useHealth();

  const paper = snapshot?.paper_mode ?? false;
  const mode = snapshot?.engine_mode;
  const breaker = snapshot?.circuit_breaker_active ?? false;
  const kiteOk = health?.kite_connected === true && health?.token_present === true;
  const ttl = typeof health?.token_expires_in_hours === "number" ? `${health.token_expires_in_hours}h left` : null;
  const snapTime = snapshot?.snapshot_time ? new Date(snapshot.snapshot_time).toLocaleTimeString() : null;
  const row = "flex items-center justify-between gap-4";

  return (
    <div className="flex flex-col gap-2 px-2.5 py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={LABEL}>Operator</div>

      <div className={row}>
        <span style={KEY}>Engine</span>
        <span style={{ ...VALUE, color: paper ? "var(--warning)" : "var(--text-primary)" }}>
          {snapshot ? `${paper ? "PAPER" : "LIVE"}${mode ? ` · ${mode}` : ""}` : "—"}
        </span>
      </div>
      {breaker && (
        <div style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--danger)" }}>⛔ Circuit breaker active</div>
      )}

      <div className={row}>
        <span style={KEY}>Kite</span>
        <span style={{ ...VALUE, color: health ? (kiteOk ? "var(--success)" : "var(--warning)") : "var(--text-dim)" }}>
          {health ? (kiteOk ? `Connected${ttl ? ` · ${ttl}` : ""}` : "Needs login") : "—"}
        </span>
      </div>
      <button
        type="button"
        onClick={() => { window.location.href = "/api/kite/login"; }}
        className="inline-flex items-center justify-center gap-1.5 rounded-md font-semibold"
        style={{
          padding: "7px 10px", fontSize: "0.74rem", cursor: "pointer",
          color: kiteOk ? "var(--text-secondary)" : "var(--accent)",
          background: kiteOk ? "transparent" : "var(--accent-dim)",
          border: `1px solid ${kiteOk ? "var(--border)" : "var(--accent)"}`,
        }}
      >
        <Wifi size={12} /> {kiteOk ? "Refresh Kite" : "Connect Kite"}
      </button>

      <div className={row}>
        <span style={KEY}>System</span>
        <span className="flex items-center gap-2" style={{ fontSize: "0.68rem", color: "var(--text-secondary)" }}>
          <span title={health?.db_connected ? "DB connected" : "DB error"}>
            <Database size={11} color={health?.db_connected ? "var(--success)" : "var(--danger)"} />
          </span>
          <span title={`${health?.ws_clients ?? 0} WebSocket client(s)`}>
            <Activity size={11} color={(health?.ws_clients ?? 0) > 0 ? "var(--success)" : "var(--warning)"} />
          </span>
          {health?.backend_version ? <span style={{ color: "var(--text-dim)" }}>v{health.backend_version}</span> : null}
        </span>
      </div>
      <div style={{ fontSize: "0.64rem", color: "var(--text-dim)" }}>
        Feed {status}{snapTime ? ` · snapshot ${snapTime}` : ""}
      </div>

      <Link
        href="/health"
        onClick={onNavigate}
        className="rounded-md"
        style={{ fontSize: "0.74rem", color: "var(--accent)", textDecoration: "none", padding: "2px 0" }}
      >
        Product Health →
      </Link>
    </div>
  );
}
