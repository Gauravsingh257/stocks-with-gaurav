"use client";
import { Zap } from "lucide-react";
import type { TacticalPlan } from "./types";

export function TacticalPlanWidget({ plan }: { plan: TacticalPlan | null }) {
  if (!plan) return null;

  const modeColors: Record<string, string> = {
    DEFENSIVE: "#ff4d6d",
    CONTROLLED: "#ffd700",
    NORMAL: "#00ff88",
    AGGRESSIVE_PLUS: "#00d4ff",
  };
  const modeColor = modeColors[plan.mode] || "var(--accent)";

  const stateColors: Record<string, string> = {
    STRONG: "#00ff88", HEALTHY: "#00ff88", NORMAL: "#00ff88",
    WARNING: "#ffd700", ELEVATED: "#ffd700", REDUCE_RISK: "#ffd700",
    CRITICAL: "#ff4d6d", DANGER: "#ff4d6d", CONSERVATIVE: "#ff4d6d",
  };

  const condColors: Record<string, string> = {
    TRENDING: "#00d4ff", RANGE: "#ffd700", VOLATILE: "#ff4d6d", EXPIRY_MANIPULATION: "#ff4d6d",
  };

  const items = [
    { label: "Risk Multiplier", value: `${plan.risk_multiplier}x` },
    { label: "Max Daily Risk", value: `${plan.max_daily_risk}R` },
    { label: "Score Threshold", value: `\u2265 ${plan.score_threshold}` },
    { label: "Stop After", value: `${plan.stop_after_losses} losses` },
  ];

  return (
    <div className="glass" style={{
      border: `1px solid ${modeColor}33`,
      borderTop: `3px solid ${modeColor}`,
      overflow: "hidden",
    }}>
      <div style={{
        padding: "16px 20px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderBottom: "1px solid var(--border)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Zap size={18} style={{ color: modeColor }} />
          <span style={{ fontWeight: 700, fontSize: "0.95rem" }}>Daily Tactical Plan</span>
          <span style={{
            fontSize: "0.7rem", padding: "2px 10px", borderRadius: 12,
            background: `${modeColor}22`, color: modeColor, fontWeight: 700,
            border: `1px solid ${modeColor}44`,
          }}>
            {plan.mode}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>{plan.date}</span>
          <div style={{
            fontSize: "0.72rem", fontWeight: 700, color: modeColor,
            display: "flex", alignItems: "center", gap: 5,
          }}>
            <span style={{ fontSize: "1rem" }}>{plan.confidence}%</span>
            <span style={{ fontSize: "0.65rem", color: "var(--text-secondary)", fontWeight: 400 }}>confidence</span>
          </div>
        </div>
      </div>

      <div style={{ padding: "16px 20px" }}>
        {/* Top metrics row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
          {items.map(it => (
            <div key={it.label} style={{
              background: "var(--bg-tertiary)", borderRadius: 8, padding: "10px 14px",
              textAlign: "center",
            }}>
              <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                {it.label}
              </div>
              <div style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                {it.value}
              </div>
            </div>
          ))}
        </div>

        {/* Classification badges */}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
          <span style={{
            fontSize: "0.7rem", padding: "3px 10px", borderRadius: 6,
            background: `${condColors[plan.market_condition] || "#666"}22`,
            color: condColors[plan.market_condition] || "#aaa",
            border: `1px solid ${condColors[plan.market_condition] || "#666"}44`,
          }}>
            Market: {plan.market_condition}
          </span>
          <span style={{
            fontSize: "0.7rem", padding: "3px 10px", borderRadius: 6,
            background: `${stateColors[plan.wr_state] || "#666"}22`,
            color: stateColors[plan.wr_state] || "#aaa",
            border: `1px solid ${stateColors[plan.wr_state] || "#666"}44`,
          }}>
            WR: {plan.wr_state}
          </span>
          <span style={{
            fontSize: "0.7rem", padding: "3px 10px", borderRadius: 6,
            background: `${stateColors[plan.dd_state] || "#666"}22`,
            color: stateColors[plan.dd_state] || "#aaa",
            border: `1px solid ${stateColors[plan.dd_state] || "#666"}44`,
          }}>
            DD: {plan.dd_state}
          </span>
          <span style={{
            fontSize: "0.7rem", padding: "3px 10px", borderRadius: 6,
            background: `${stateColors[plan.cl_state] || "#666"}22`,
            color: stateColors[plan.cl_state] || "#aaa",
            border: `1px solid ${stateColors[plan.cl_state] || "#666"}44`,
          }}>
            Losses: {plan.cl_state}
          </span>
          <span style={{
            fontSize: "0.7rem", padding: "3px 10px", borderRadius: 6,
            background: "var(--bg-tertiary)", color: "var(--text-secondary)",
          }}>
            Regime: {plan.market_regime}
          </span>
        </div>

        {/* Setup Focus / Disable */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div>
            <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)", marginBottom: 5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Focus Setups
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {plan.focus_setups.length > 0 ? plan.focus_setups.map(s => (
                <span key={s} style={{
                  fontSize: "0.72rem", padding: "2px 8px", borderRadius: 4,
                  background: "rgba(0,255,136,0.1)", color: "#00ff88",
                  border: "1px solid rgba(0,255,136,0.2)",
                }}>
                  {s}
                </span>
              )) : (
                <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>All active</span>
              )}
            </div>
          </div>
          <div>
            <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)", marginBottom: 5, textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Disabled Setups
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {plan.disable_setups.length > 0 ? plan.disable_setups.map(s => (
                <span key={s} style={{
                  fontSize: "0.72rem", padding: "2px 8px", borderRadius: 4,
                  background: "rgba(255,77,109,0.1)", color: "#ff4d6d",
                  border: "1px solid rgba(255,77,109,0.2)",
                  textDecoration: "line-through",
                }}>
                  {s}
                </span>
              )) : (
                <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>None</span>
              )}
            </div>
          </div>
        </div>

        {/* Mode description */}
        <div style={{
          marginTop: 14, fontSize: "0.73rem", color: "var(--text-secondary)",
          background: "var(--bg-tertiary)", borderRadius: 6, padding: "8px 12px",
          borderLeft: `3px solid ${modeColor}`,
        }}>
          {plan.mode_description}
        </div>
      </div>
    </div>
  );
}
