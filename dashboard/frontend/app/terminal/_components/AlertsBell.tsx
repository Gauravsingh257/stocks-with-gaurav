"use client";

/**
 * AlertsBell — lifecycle alert notification bell.
 *
 * Shows unread alert count. Click to open a dropdown with the last 20 alerts.
 * Receives new alerts in real-time via the `newAlerts` prop (from useLiveTrades
 * WebSocket event frames).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { getBackendBase } from "@/lib/api";

interface AlertItem {
  symbol: string;
  old_state?: string;
  new_state: string;
  message: string;
  type: "info" | "success" | "danger" | "warn";
  cmp?: number | null;
  ts: number;
}

interface Props {
  /** Pass new alert objects from the WebSocket stream to bump count. */
  newAlert?: AlertItem | null;
}

const TYPE_COLOR: Record<string, string> = {
  success: "#00e096",
  info: "#00d4ff",
  danger: "#ff4757",
  warn: "#ffa502",
};

export default function AlertsBell({ newAlert }: Props) {
  const base = getBackendBase();
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await fetch(`${base}/api/alerts?limit=20`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setAlerts((data.alerts ?? []) as AlertItem[]);
      }
    } catch {
      /* ignore */
    }
  }, [base]);

  // Bump unread count on new websocket alert
  useEffect(() => {
    if (!newAlert) return;
    setAlerts((prev) => [newAlert, ...prev.slice(0, 19)]);
    if (!open) setUnread((n) => n + 1);
  }, [newAlert, open]);

  // Load on mount
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  function handleOpen() {
    setOpen((v) => !v);
    if (!open) {
      setUnread(0);
      fetchAlerts();
    }
  }

  return (
    <div ref={dropdownRef} style={{ position: "relative" }}>
      {/* Bell button */}
      <button
        type="button"
        onClick={handleOpen}
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: 36,
          height: 36,
          borderRadius: 10,
          border: "1px solid var(--border)",
          background: open ? "rgba(0,212,255,0.1)" : "rgba(255,255,255,0.04)",
          color: open ? "#00d4ff" : "var(--text-secondary)",
          cursor: "pointer",
          transition: "background 0.15s, color 0.15s",
        }}
        aria-label="Alerts"
      >
        <Bell size={16} />
        {unread > 0 && (
          <span
            style={{
              position: "absolute",
              top: -5,
              right: -5,
              minWidth: 16,
              height: 16,
              borderRadius: 999,
              background: "#ff4757",
              color: "#fff",
              fontSize: "0.55rem",
              fontWeight: 800,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: "0 3px",
              border: "1.5px solid var(--bg-primary)",
            }}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div
          style={{
            position: "absolute",
            top: 42,
            right: 0,
            width: 300,
            maxHeight: 380,
            overflowY: "auto",
            borderRadius: 14,
            border: "1px solid var(--border)",
            background: "var(--bg-secondary)",
            boxShadow: "0 16px 40px rgba(0,0,0,0.45)",
            zIndex: 1000,
            padding: "6px 0",
          }}
        >
          <div
            style={{
              padding: "8px 14px 6px",
              fontSize: "0.62rem",
              fontWeight: 700,
              letterSpacing: 1,
              color: "var(--text-dim)",
              textTransform: "uppercase",
              borderBottom: "1px solid var(--border)",
              marginBottom: 4,
            }}
          >
            Recent Alerts
          </div>

          {alerts.length === 0 ? (
            <div
              style={{
                padding: "16px 14px",
                fontSize: "0.72rem",
                color: "var(--text-dim)",
                textAlign: "center",
              }}
            >
              No alerts yet
            </div>
          ) : (
            alerts.map((a, idx) => (
              <AlertRow key={`${a.symbol}-${a.ts}-${idx}`} alert={a} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function AlertRow({ alert }: { alert: AlertItem }) {
  const color = TYPE_COLOR[alert.type] ?? "#8899bb";
  const timeAgo = formatTimeAgo(alert.ts);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "8px 14px",
        borderBottom: "1px solid rgba(255,255,255,0.04)",
        transition: "background 0.1s",
      }}
    >
      {/* Color dot */}
      <span
        style={{
          marginTop: 3,
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          boxShadow: `0 0 6px ${color}88`,
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            gap: 6,
          }}
        >
          <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "var(--text-primary)" }}>
            {alert.symbol}
          </span>
          <span style={{ fontSize: "0.58rem", color: "var(--text-dim)", flexShrink: 0 }}>{timeAgo}</span>
        </div>
        <div style={{ fontSize: "0.66rem", color: "var(--text-secondary)", marginTop: 1 }}>
          {alert.message}
        </div>
        {alert.new_state && (
          <div style={{ fontSize: "0.58rem", color, marginTop: 2, fontWeight: 600, letterSpacing: 0.5 }}>
            {alert.new_state}
          </div>
        )}
      </div>
    </div>
  );
}

function formatTimeAgo(ts: number): string {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
