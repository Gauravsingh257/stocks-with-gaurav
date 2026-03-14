"use client";
import { Database, Activity, Cpu, Zap } from "lucide-react";
import type { HealthData } from "./types";

export function HealthWidget({ health }: { health: HealthData | null }) {
  if (!health) return (
    <div className="glass" style={{ padding: "20px 24px", opacity: 0.5, fontSize: "0.8rem", color: "var(--text-secondary)" }}>
      Fetching system health\u2026
    </div>
  );

  const items = [
    { label: "Database",   ok: health.db_connected,     detail: health.db_connected ? `${health.db_trade_rows} trades` : "Error",        icon: <Database size={13} /> },
    { label: "WebSocket",  ok: health.ws_clients >= 0,  detail: `${health.ws_clients} client(s)`,                                         icon: <Activity size={13} /> },
    { label: "Engine",     ok: health.engine_live,       detail: health.engine_live ? health.engine_mode : "STANDALONE",                   icon: <Cpu size={13} /> },
    { label: "Scheduler",  ok: health.scheduler_running, detail: health.scheduler_running ? "Running" : "Stopped",                        icon: <Zap size={13} /> },
  ];

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontWeight: 600, fontSize: "0.88rem" }}>System Health</span>
        <div style={{ display: "flex", gap: 12, fontSize: "0.7rem", color: "var(--text-secondary)" }}>
          <span>Backend v{health.backend_version}</span>
          <span>Engine {health.engine_version}</span>
          <span>Up {health.uptime_human}</span>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 0 }}>
        {items.map((item, i) => (
          <div key={item.label} style={{
            padding: "14px 20px",
            borderRight: i < items.length - 1 ? "1px solid var(--border)" : "none",
            display: "flex", alignItems: "center", gap: 12,
          }}>
            <div style={{
              width: 34, height: 34, borderRadius: 8,
              background: item.ok ? "rgba(0,255,136,0.1)" : "rgba(255,77,109,0.1)",
              border: `1px solid ${item.ok ? "#00ff8833" : "#ff4d6d33"}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              color: item.ok ? "#00ff88" : "#ff4d6d",
            }}>
              {item.icon}
            </div>
            <div>
              <div style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>{item.label}</div>
              <div style={{ fontWeight: 600, fontSize: "0.82rem", color: item.ok ? "#00ff88" : "#ff4d6d" }}>
                {item.detail}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
