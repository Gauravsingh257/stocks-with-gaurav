"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Activity, CheckCircle, Sparkles, TrendingUp, Waves, XCircle, Zap } from "lucide-react";
import type { LiveEvent } from "../_lib/useLiveTrades";

interface Props {
  events: LiveEvent[];
  onSelectSymbol: (symbol: string) => void;
}

// ─── Event-type catalogue ─────────────────────────────────────────────────

interface EventMeta {
  title: string;
  color: string;
  icon: React.ReactNode;
  isNew?: boolean;
}

function getEventMeta(type: string): EventMeta {
  const t = type.toUpperCase();
  if (t === "NEW_SETUP")
    return { title: "New Setup Detected", color: "#00e096", icon: <Sparkles size={13} />, isNew: true };
  if (t === "ENTRY_TRIGGER")
    return { title: "Entry Triggered", color: "#00d4ff", icon: <Zap size={13} />, isNew: true };
  if (t === "LIQUIDITY_SWEEP" || t === "SWEEP")
    return { title: "Liquidity Sweep", color: "#a78bfa", icon: <Waves size={13} /> };
  if (t === "TARGET_HIT")
    return { title: "Target Hit ✓", color: "#00e096", icon: <CheckCircle size={13} /> };
  if (t === "STOP_HIT")
    return { title: "Stop Hit", color: "#ff4757", icon: <XCircle size={13} /> };
  if (t === "APPROACHING")
    return { title: "Approaching Zone", color: "#ffa502", icon: <Activity size={13} /> };
  return { title: type.replace(/_/g, " "), color: "#8899bb", icon: <TrendingUp size={13} /> };
}

function relativeTime(isoOrSecs: string | number): string {
  try {
    const ts = typeof isoOrSecs === "number" ? isoOrSecs * 1000 : new Date(isoOrSecs).getTime();
    const diff = Math.floor((Date.now() - ts) / 1000);
    if (diff < 5) return "just now";
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
  } catch {
    return "";
  }
}

export default function DiscoveryFeed({ events, onSelectSymbol }: Props) {
  const feed = events.slice(0, 20);

  return (
    <div>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: "0.62rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1.1 }}>
            Live Feed
          </div>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 800, color: "var(--text-primary)" }}>
            Discovery Stream
          </h2>
        </div>
        <span style={{ fontSize: "0.66rem", color: "var(--text-dim)" }}>
          {feed.length > 0 ? `${feed.length} events` : "Awaiting events…"}
        </span>
      </header>

      {feed.length === 0 ? (
        <div
          style={{
            padding: 18,
            background: "rgba(255,255,255,0.02)",
            border: "1px dashed var(--border)",
            borderRadius: 12,
            textAlign: "center",
            fontSize: "0.74rem",
            color: "var(--text-dim)",
          }}
        >
          No events yet — engine is scanning. New setups, sweeps, and triggers will appear here in real-time.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <AnimatePresence initial={false}>
            {feed.map((event) => {
              const meta = getEventMeta(event.type);
              const p = event.payload ?? {};
              const direction = String(p.direction ?? "").toUpperCase();
              const isBuy = direction === "LONG" || direction === "BUY";
              const dirColor = direction ? (isBuy ? "#00e096" : "#ff4757") : undefined;
              const rr = p.rr != null ? Number(p.rr) : null;

              return (
                <motion.button
                  key={`${event.type}-${event.symbol}-${event.ts}`}
                  type="button"
                  layout
                  initial={{ opacity: 0, y: -12, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, height: 0, marginBottom: 0, overflow: "hidden" }}
                  transition={{ duration: 0.28, ease: [0.21, 0.5, 0.3, 1] }}
                  whileHover={{ x: 2 }}
                  onClick={() => onSelectSymbol(event.symbol)}
                  style={{
                    textAlign: "left",
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto",
                    gap: 10,
                    alignItems: "center",
                    padding: "10px 12px",
                    background: meta.isNew ? `${meta.color}0a` : "rgba(255,255,255,0.025)",
                    border: `1px solid ${meta.isNew ? `${meta.color}30` : "var(--border)"}`,
                    borderRadius: 11,
                    color: "var(--text-primary)",
                    cursor: "pointer",
                    position: "relative",
                    overflow: "hidden",
                  }}
                >
                  {/* Left accent */}
                  <span
                    aria-hidden
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: 3,
                      background: meta.color,
                      boxShadow: meta.isNew ? `0 0 10px ${meta.color}` : "none",
                    }}
                  />

                  {/* Icon */}
                  <div
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: 9,
                      background: `${meta.color}1a`,
                      border: `1px solid ${meta.color}44`,
                      color: meta.color,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    {meta.icon}
                  </div>

                  {/* Content */}
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
                      <span style={{ fontWeight: 700, fontSize: "0.76rem", color: meta.color }}>{meta.title}</span>
                      <span style={{ fontSize: "0.7rem", fontWeight: 700, color: "var(--text-primary)" }}>
                        {event.symbol}
                      </span>
                      {direction && (
                        <span
                          style={{
                            fontSize: "0.58rem",
                            padding: "1px 6px",
                            borderRadius: 4,
                            fontWeight: 700,
                            background: dirColor ? `${dirColor}20` : "rgba(255,255,255,0.08)",
                            color: dirColor ?? "var(--text-dim)",
                            letterSpacing: 0.5,
                          }}
                        >
                          {isBuy ? "LONG" : "SHORT"}
                        </span>
                      )}
                      {rr != null && (
                        <span style={{ fontSize: "0.58rem", color: "var(--text-dim)" }}>
                          {rr.toFixed(1)}R
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: "0.63rem", color: "var(--text-dim)", marginTop: 2 }}>
                      {relativeTime(event.ts || event.time)}
                    </div>
                  </div>

                  {/* Setup badge if present */}
                  {p.setup && (
                    <span
                      style={{
                        fontSize: "0.6rem",
                        fontWeight: 800,
                        padding: "2px 7px",
                        borderRadius: 6,
                        background: "rgba(255,255,255,0.06)",
                        border: "1px solid var(--border)",
                        color: "var(--text-secondary)",
                        letterSpacing: 0.4,
                        flexShrink: 0,
                      }}
                    >
                      {String(p.setup)}
                    </span>
                  )}
                </motion.button>
              );
            })}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
