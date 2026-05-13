"use client";

import { useEffect, useState } from "react";
import { Send, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

type LiveSignal = {
  signal_id?: string;
  symbol?: string;
  direction?: string;
  strategy_name?: string;
  entry?: number | null;
  stop_loss?: number | null;
  target1?: number | null;
  target2?: number | null;
  score?: number | null;
  confidence?: number | null;
  timestamp?: string;
  signal_kind?: string;
};

function fmtPrice(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(2);
}

function fmtAge(ts: string | undefined): string {
  if (!ts) return "—";
  const t = new Date(ts.replace(" ", "T")).getTime();
  if (Number.isNaN(t)) return ts;
  const min = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

export default function LiveSignals() {
  const [items, setItems] = useState<LiveSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .liveSignals(20)
      .then((r) => {
        setItems(r.items || []);
        setError(null);
      })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  if (loading && items.length === 0) {
    return null;
  }

  return (
    <section
      style={{
        background: "rgba(8,16,32,0.6)",
        border: "1px solid rgba(0,212,255,0.18)",
        borderRadius: 12,
        padding: "14px 16px",
        marginBottom: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <Send size={14} color="#00d4ff" />
        <strong style={{ fontSize: "0.8rem", letterSpacing: 0.4, color: "#c4f1ff" }}>
          LIVE SIGNALS (TELEGRAM MIRROR)
        </strong>
        <span style={{ fontSize: "0.65rem", color: "var(--text-dim)" }}>
          Same signals delivered to Telegram, same time.
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={load}
          aria-label="Refresh"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--text-dim)",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: "0.7rem",
          }}
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>
      {error && (
        <div style={{ fontSize: "0.7rem", color: "var(--warning)" }}>
          Could not load live signals: {error}
        </div>
      )}
      {items.length === 0 && !error && (
        <div style={{ fontSize: "0.72rem", color: "var(--text-dim)" }}>
          No signals delivered today yet. The engine is monitoring during market hours
          (09:15–15:30 IST).
        </div>
      )}
      {items.length > 0 && (
        <div style={{ display: "grid", gap: 8 }}>
          {items.slice(0, 8).map((s, idx) => (
            <div
              key={s.signal_id || idx}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto auto auto auto",
                gap: 12,
                alignItems: "center",
                padding: "8px 10px",
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.05)",
                borderRadius: 8,
                fontSize: "0.74rem",
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ color: "var(--text-primary)" }}>
                  {String(s.symbol || "?").replace(/^NSE:/, "")}
                </strong>
                <span style={{ fontSize: "0.62rem", color: "var(--text-dim)" }}>
                  {s.strategy_name || s.signal_kind || "Signal"} · {fmtAge(s.timestamp)}
                </span>
              </div>
              <span
                style={{
                  fontSize: "0.62rem",
                  fontWeight: 800,
                  letterSpacing: 0.1,
                  color:
                    s.direction === "LONG"
                      ? "#00e096"
                      : s.direction === "SHORT"
                        ? "#ff5e7e"
                        : "var(--text-dim)",
                }}
              >
                {s.direction || "—"}
              </span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>
                Entry <strong>{fmtPrice(s.entry)}</strong>
              </span>
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--text-dim)" }}>
                SL {fmtPrice(s.stop_loss)}
              </span>
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--accent)" }}>
                T1 {fmtPrice(s.target1)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
