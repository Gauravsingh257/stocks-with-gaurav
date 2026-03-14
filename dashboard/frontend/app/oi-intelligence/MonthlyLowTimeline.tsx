"use client";
import { Target } from "lucide-react";
import { fmt, timeAgo, type MonthlyTap } from "./types";

export function MonthlyLowTimeline({ taps }: { taps: MonthlyTap[] }) {
  if (taps.length === 0) {
    return (
      <div className="glass" style={{ padding: 20 }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600, marginBottom: 12 }}>
          <Target size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }} />
          MONTHLY LOW TAP MONITOR
        </div>
        <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 30 }}>
          No monthly low taps detected yet
        </div>
      </div>
    );
  }

  return (
    <div className="glass" style={{ padding: 20 }}>
      <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600, marginBottom: 16 }}>
        <Target size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }} />
        MONTHLY LOW TAP MONITOR
        <span className="badge badge-neutral" style={{ marginLeft: 8 }}>{taps.length} tracked</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {taps.map((t, i) => {
          const isTapped = t.state === "tapped" || t.state === "bounced";
          const isBounced = t.state === "bounced";
          const isCooldown = t.state === "cooldown";

          return (
            <div key={`${t.symbol}-${t.strike}-${i}`}
              className={isTapped ? "oi-signal-pulse" : ""}
              style={{
                display: "flex", alignItems: "center", gap: 14,
                padding: "12px 16px", borderRadius: 8,
                background: isTapped ? "rgba(0,212,255,0.06)" : isCooldown ? "rgba(255,165,2,0.04)" : "rgba(255,255,255,0.02)",
                border: `1px solid ${isTapped ? "rgba(0,212,255,0.2)" : isCooldown ? "rgba(255,165,2,0.15)" : "rgba(255,255,255,0.05)"}`,
              }}
            >
              <div style={{
                width: 10, height: 10, borderRadius: "50%",
                background: isBounced ? "var(--success)" : isTapped ? "var(--accent)" : isCooldown ? "var(--warning)" : "var(--muted)",
                boxShadow: isTapped ? "0 0 8px rgba(0,212,255,0.4)" : undefined,
                flexShrink: 0,
              }} className={isTapped ? "pulse-dot" : ""} />

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: "0.85rem" }}>{t.symbol}</span>
                  <span style={{ fontFamily: "monospace", fontSize: "0.8rem", color: "var(--accent)" }}>{t.strike} {t.opt_type}</span>
                  <span className={`badge ${isBounced ? "badge-win" : isTapped ? "badge-long" : isCooldown ? "badge-paper" : "badge-neutral"}`} style={{ fontSize: "0.6rem" }}>
                    {t.state?.toUpperCase() || "TRACKING"}
                  </span>
                </div>
                <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginTop: 2, display: "flex", gap: 12 }}>
                  <span>Low: ₹{fmt(t.monthly_low, 1)}</span>
                  <span>Current: ₹{fmt(t.current_price, 1)}</span>
                  {t.bounce_pct !== undefined && (
                    <span style={{ color: t.bounce_pct > 0 ? "var(--success)" : "var(--danger)" }}>
                      Bounce: {fmt(t.bounce_pct, 1)}%
                    </span>
                  )}
                  {t.tap_time && <span>{timeAgo(t.tap_time)}</span>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
