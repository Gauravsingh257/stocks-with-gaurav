"use client";
import { TrendingUp, TrendingDown, Minus, ArrowUp, ArrowDown, Activity, Zap } from "lucide-react";
import { fmt, pcrZone, type UnderlyingSummary } from "./types";

function interpretBias(s: UnderlyingSummary): { text: string; sub: string } {
  const bull = s.bull_score ?? 0;
  const bear = s.bear_score ?? 0;
  const pcr = s.pcr ?? 1;
  const trend = s.pcr_trend ?? "";
  if (bull === 0 && bear === 0) return { text: "No strong signals yet", sub: "Waiting for OI activity" };
  if (bull > bear && trend === "RISING")  return { text: "Bulls loading puts — strong hedge demand", sub: "PCR rising confirms accumulation" };
  if (bull > bear)                         return { text: "Put OI dominant — longs hedging positions", sub: "Broadly supportive for upside" };
  if (bear > bull && trend === "FALLING") return { text: "Puts being shed — bearish momentum building", sub: "PCR falling confirms selling pressure" };
  if (bear > bull)                         return { text: "Call OI dominant — sellers at resistance", sub: "Overhead supply capping upside" };
  if (pcr > 1.5)  return { text: "Extreme put loading — potential short squeeze setup", sub: "PCR historically elevated" };
  if (pcr < 0.7)  return { text: "Low put protection — market overconfident", sub: "Watch for reversal trigger" };
  return { text: "Balanced positioning — no clear edge", sub: "Wait for breakout in OI activity" };
}

function TrendIcon({ trend }: { trend: string }) {
  const t = trend?.toUpperCase() || "";
  if (t === "RISING")  return <ArrowUp size={13} style={{ color: "var(--success)" }} />;
  if (t === "FALLING") return <ArrowDown size={13} style={{ color: "var(--danger)" }} />;
  return <Minus size={11} style={{ color: "var(--text-dim)" }} />;
}

function BiasIcon({ bias }: { bias: string }) {
  const b = bias?.toUpperCase() || "";
  if (b.includes("BULL")) return <TrendingUp size={15} />;
  if (b.includes("BEAR")) return <TrendingDown size={15} />;
  return <Minus size={15} />;
}

export function UnderlyingSummaryCards({ summaries }: { summaries: Record<string, UnderlyingSummary> }) {
  const entries = Object.entries(summaries);
  if (entries.length === 0) {
    return (
      <div className="glass" style={{ padding: 24, textAlign: "center", color: "var(--text-dim)" }}>
        No underlying data available
      </div>
    );
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 14 }}>
      {entries.map(([name, s]) => {
        const zone = pcrZone(s.pcr);
        const bull = s.bull_score ?? 0;
        const bear = s.bear_score ?? 0;
        const total = bull + bear || 1;
        const bullPct = Math.round((bull / total) * 100);
        const bearPct = 100 - bullPct;
        const isBull = s.bias?.includes("BULL");
        const isBear = s.bias?.includes("BEAR");
        const accentColor = isBull ? "var(--success)" : isBear ? "var(--danger)" : "var(--warning)";
        const { text: interpretation, sub: interpretSub } = interpretBias(s);

        return (
          <div key={name} className="glass oi-card-enter" style={{
            padding: 0, position: "relative", overflow: "hidden",
            border: `1px solid ${isBull ? "rgba(0,224,150,0.25)" : isBear ? "rgba(255,71,87,0.25)" : "rgba(255,255,255,0.06)"}`,
          }}>
            {/* Accent top bar */}
            <div style={{ height: 3, background: `linear-gradient(90deg, ${accentColor}, transparent)` }} />

            <div style={{ padding: "16px 18px" }}>
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Activity size={16} style={{ color: accentColor }} />
                  <span style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--text-primary)" }}>{name}</span>
                </div>
                <span className={`badge ${isBull ? "badge-win" : isBear ? "badge-loss" : "badge-neutral"}`}
                  style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.7rem", padding: "3px 8px" }}>
                  <BiasIcon bias={s.bias} /> {s.bias || "NEUTRAL"}
                </span>
              </div>

              {/* PCR Row */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                <div>
                  <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>Put/Call Ratio</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                    <span style={{ fontSize: "1.5rem", fontWeight: 800, fontFamily: "monospace", color: zone.color, lineHeight: 1 }}>
                      {fmt(s.pcr, 3)}
                    </span>
                    <span style={{ fontSize: "0.65rem", fontWeight: 600, color: zone.color, background: `${zone.color}18`, padding: "2px 6px", borderRadius: 4, border: `1px solid ${zone.color}40` }}>
                      {zone.label}
                    </span>
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>PCR Trend</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 5, justifyContent: "flex-end", fontSize: "0.78rem", fontWeight: 600, color: s.pcr_trend === "RISING" ? "var(--success)" : s.pcr_trend === "FALLING" ? "var(--danger)" : "var(--text-dim)" }}>
                    <TrendIcon trend={s.pcr_trend} /> {s.pcr_trend || "FLAT"}
                  </div>
                </div>
              </div>

              {/* Bull vs Bear conviction bar */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.62rem", marginBottom: 4 }}>
                  <span style={{ color: "var(--success)", fontWeight: 600 }}>🐂 BULL {bullPct}%</span>
                  <span style={{ color: "var(--text-dim)", fontSize: "0.6rem" }}>Conviction</span>
                  <span style={{ color: "var(--danger)", fontWeight: 600 }}>{bearPct}% BEAR 🐻</span>
                </div>
                <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", gap: 1 }}>
                  <div style={{
                    width: `${bullPct}%`, background: "linear-gradient(90deg, #00e096, #00b87a)",
                    borderRadius: "4px 0 0 4px", transition: "width 0.4s ease",
                    minWidth: bull > 0 ? 4 : 0,
                  }} />
                  <div style={{
                    flex: 1, background: "linear-gradient(90deg, #c0392b, #ff4757)",
                    borderRadius: "0 4px 4px 0", transition: "width 0.4s ease",
                    minWidth: bear > 0 ? 4 : 0,
                  }} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.6rem", marginTop: 3, color: "var(--text-dim)" }}>
                  <span>Score: {bull}</span>
                  <span>Score: {bear}</span>
                </div>
              </div>

              {/* Interpretation */}
              <div style={{
                background: `${accentColor}0d`, border: `1px solid ${accentColor}22`,
                borderRadius: 6, padding: "8px 10px",
              }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
                  <Zap size={12} style={{ color: accentColor, marginTop: 2, flexShrink: 0 }} />
                  <div>
                    <div style={{ fontSize: "0.7rem", fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.4 }}>{interpretation}</div>
                    <div style={{ fontSize: "0.63rem", color: "var(--text-dim)", marginTop: 2 }}>{interpretSub}</div>
                  </div>
                </div>
              </div>

              {/* Short Covering pill */}
              {s.sc_active && (
                <div style={{
                  marginTop: 10, padding: "6px 10px", borderRadius: 6,
                  background: "rgba(0,224,150,0.08)", border: "1px solid rgba(0,224,150,0.25)",
                  fontSize: "0.7rem", display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--success)", display: "inline-block", boxShadow: "0 0 6px var(--success)", animation: "pulse 1.5s infinite" }} />
                  <span style={{ color: "var(--success)", fontWeight: 600 }}>{s.sc_count} Short Covering Signal{s.sc_count !== 1 ? "s" : ""} Active</span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
