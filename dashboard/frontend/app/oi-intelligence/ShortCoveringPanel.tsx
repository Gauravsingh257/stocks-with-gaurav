"use client";
import { Activity, Shield, Zap } from "lucide-react";
import { HexPattern } from "@/components/FuturisticElements";
import { fmt, type ShortCoveringSignal } from "./types";

export function ShortCoveringPanel({ signals }: { signals: ShortCoveringSignal[] }) {
  return (
    <div className="glass" style={{ padding: 20, position: "relative", overflow: "hidden", minHeight: 200 }}>
      <HexPattern style={{ opacity: 0.02 }} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600 }}>
          <Zap size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }} />
          SHORT COVERING SIGNALS
        </div>
        {signals.length > 0 && (
          <span className="badge badge-win">
            <Activity size={10} /> {signals.length} ACTIVE
          </span>
        )}
      </div>

      {signals.length === 0 ? (
        <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 30 }}>
          <Shield size={32} style={{ margin: "0 auto 8px", opacity: 0.3 }} />
          <div>No short covering signals detected</div>
          <div style={{ fontSize: "0.7rem", marginTop: 4 }}>Signals appear when OI drops with rising prices</div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
          {signals.map((s, i) => (
            <div key={`${s.tradingsymbol}-${i}`}
              className="oi-signal-pulse oi-card-enter"
              style={{
                padding: "14px 16px", borderRadius: 8,
                background: "rgba(0,224,150,0.05)",
                border: "1px solid rgba(0,224,150,0.2)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontWeight: 700, fontSize: "0.85rem" }}>
                  {s.tradingsymbol || `${s.underlying} ${s.strike} ${s.opt_type}`}
                </div>
                <div style={{
                  padding: "2px 8px", borderRadius: 4,
                  background: s.score >= 8 ? "rgba(0,224,150,0.2)" : s.score >= 5 ? "rgba(255,165,2,0.2)" : "rgba(255,255,255,0.06)",
                  fontSize: "0.75rem", fontWeight: 700,
                  color: s.score >= 8 ? "var(--success)" : s.score >= 5 ? "var(--warning)" : "var(--text-secondary)",
                  fontFamily: "monospace",
                }}>
                  {s.score}/10
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: "0.72rem" }}>
                <div>
                  <span style={{ color: "var(--text-dim)" }}>OI Drop: </span>
                  <span style={{ color: "var(--success)", fontWeight: 600 }}>-{fmt(s.oi_drop_pct * 100, 1)}%</span>
                </div>
                <div>
                  <span style={{ color: "var(--text-dim)" }}>Price Rise: </span>
                  <span style={{ color: "var(--success)", fontWeight: 600 }}>+{fmt(s.price_rise_pct * 100, 1)}%</span>
                </div>
                <div>
                  <span style={{ color: "var(--text-dim)" }}>Spot: </span>
                  <span style={{ fontFamily: "monospace" }}>₹{fmt(s.spot, 0)}</span>
                </div>
                <div>
                  <span style={{ color: "var(--text-dim)" }}>Action: </span>
                  <span className="badge badge-win" style={{ fontSize: "0.6rem" }}>{s.trade_action}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
