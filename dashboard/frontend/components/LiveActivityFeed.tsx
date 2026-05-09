"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useEngineSocket } from "@/lib/useWebSocket";
import { useHealth } from "@/lib/useHealth";
import { api, type SignalLogEntry } from "@/lib/api";

export interface LiveActivityFeedProps {
  maxHeight?: number;
  className?: string;
}

type FeedLine = { id: string; ts: number; kind: "info" | "signal" | "intel"; text: string };

export default function LiveActivityFeed({ maxHeight = 104, className }: LiveActivityFeedProps) {
  const { snapshot } = useEngineSocket();
  const health = useHealth();
  const [signals, setSignals] = useState<SignalLogEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      api
        .signals({ limit: 25 })
        .then((p) => {
          if (!cancelled) setSignals(p.signals ?? []);
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 25_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const lines = useMemo(() => {
    const out: FeedLine[] = [];
    const now = Date.now();

    if (health?.market_status) {
      out.push({
        id: "mkt",
        ts: now,
        kind: "info",
        text: `Market session: ${String(health.market_status).toUpperCase()} · backend ${health.latency_ms ?? "—"}ms`,
      });
    }
    if (typeof snapshot?.engine_heartbeat_age_sec === "number") {
      out.push({
        id: "hb",
        ts: now,
        kind: "info",
        text: `Heartbeat · ${snapshot.engine_heartbeat_age_sec.toFixed(0)}s since pulse`,
      });
    }
    if (snapshot?.market_regime) {
      out.push({
        id: "reg",
        ts: now,
        kind: "info",
        text: `Bias snapshot · ${snapshot.market_regime} regime`,
      });
    }

    const intel = snapshot?.adaptive_intel;
    const blocks = intel?.recent_blocks;
    if (Array.isArray(blocks)) {
      blocks.slice(0, 6).forEach((b, i) => {
        out.push({
          id: `b-${i}-${b.ts}`,
          ts: now - i * 1000,
          kind: "intel",
          text: `Adaptive · ${b.symbol} ${b.setup} ${b.direction}${b.reason ? ` · ${b.reason}` : ""}`,
        });
      });
    }

    signals.forEach((s, i) => {
      const sym = s.symbol ?? "—";
      const dir = s.direction ?? "";
      const kind = s.signal_kind || "signal";
      const ch = s.delivery_channel || "telegram";
      const t = s.timestamp ? new Date(String(s.timestamp).replace(" ", "T")).getTime() : now - i * 2000;
      out.push({
        id: s.signal_id || `sig-${i}`,
        ts: t,
        kind: "signal",
        text: `${ch.toUpperCase()} · ${kind} · ${sym} ${dir}`.trim(),
      });
    });

    out.sort((a, b) => b.ts - a.ts);
    return out.slice(0, 40);
  }, [snapshot, health, signals]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = 0;
  }, [lines.length]);

  return (
    <div
      className={className}
      style={{
        borderRadius: 12,
        border: "1px solid rgba(0,212,255,0.14)",
        background: "linear-gradient(180deg, rgba(8,14,28,0.92), rgba(5,10,22,0.88))",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 12px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          fontSize: "0.62rem",
          fontWeight: 800,
          letterSpacing: 0.14,
          color: "#9ecbff",
          textTransform: "uppercase",
        }}
      >
        <span>Live activity</span>
        <span style={{ color: "var(--text-dim)", fontWeight: 600 }}>Auto-updates</span>
      </div>
      <div
        ref={scrollRef}
        style={{
          maxHeight,
          overflowY: "auto",
          padding: "8px 10px",
          fontSize: "0.72rem",
          lineHeight: 1.45,
        }}
      >
        {lines.length === 0 ? (
          <div style={{ color: "var(--text-dim)", padding: "6px 4px" }}>Connecting to live feed…</div>
        ) : (
          lines.map((line) => (
            <div
              key={line.id}
              style={{
                padding: "5px 6px",
                marginBottom: 4,
                borderRadius: 6,
                background:
                  line.kind === "signal"
                    ? "rgba(0,224,150,0.06)"
                    : line.kind === "intel"
                      ? "rgba(0,212,255,0.06)"
                      : "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.04)",
                color: "var(--text-secondary)",
              }}
            >
              <span
                style={{
                  color: "var(--text-dim)",
                  marginRight: 8,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {new Date(line.ts).toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              </span>
              {line.text}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
