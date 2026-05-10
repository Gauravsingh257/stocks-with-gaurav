"use client";

/**
 * Phase B — reduced surface: bias, levels, momentum/strength only.
 * Full narrative stays behind <details> on the parent page.
 */
import { Crosshair, Shield, TrendingUp } from "lucide-react";
import type { OIInterpretation } from "./types";

export function OIInterpretationEssentials({ data }: { data: OIInterpretation | null | undefined }) {
  if (!data) return null;

  const regime = data.market_regime;
  const flow = data.flow_strength;
  const g =
    data.guidance?.NIFTY ??
    data.guidance?.["NIFTY 50"] ??
    data.guidance?.BANKNIFTY;
  const bias = regime?.label ?? data.market_story?.headline ?? "—";
  const strength = flow?.pressure_strength_score ?? flow?.scores?.persistence;
  const momentum = flow?.pressure_strength ?? flow?.bullish_pressure_trend;

  return (
    <div
      className="glass"
      style={{
        padding: "14px 16px",
        borderRadius: 12,
        border: "1px solid rgba(0,212,255,0.15)",
        display: "grid",
        gap: 12,
        fontSize: "0.78rem",
        color: "var(--text-secondary)",
      }}
    >
      <div style={{ fontSize: "0.58rem", fontWeight: 800, color: "var(--text-dim)", letterSpacing: "0.08em" }}>
        OI ESSENTIALS (LIVE BIAS & LEVELS)
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "center" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <TrendingUp size={14} color="var(--accent)" />
          <strong style={{ color: "var(--text-primary)" }}>Bias:</strong> {bias}
        </span>
        {typeof strength === "number" && !Number.isNaN(strength) && (
          <span>
            <strong style={{ color: "var(--text-primary)" }}>Strength:</strong> {Math.round(strength)}%
          </span>
        )}
        {momentum && (
          <span>
            <strong style={{ color: "var(--text-primary)" }}>Momentum:</strong> {momentum}
          </span>
        )}
      </div>
      {g && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
          {g.bullish_above != null && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Crosshair size={12} color="var(--success)" />
              Support / bullish above: <strong style={{ color: "var(--text-primary)" }}>{g.bullish_above}</strong>
            </span>
          )}
          {g.bearish_below != null && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Crosshair size={12} color="var(--danger)" />
              Resistance / bearish below: <strong style={{ color: "var(--text-primary)" }}>{g.bearish_below}</strong>
            </span>
          )}
          {g.breakout_trigger != null && (
            <span>
              Breakout trigger: <strong style={{ color: "var(--text-primary)" }}>{g.breakout_trigger}</strong>
            </span>
          )}
          {Array.isArray(g.trap_zone) && g.trap_zone.length > 0 && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Shield size={12} color="var(--warning)" />
              Trap zone: <strong style={{ color: "var(--warning)" }}>{g.trap_zone.join(" · ")}</strong>
            </span>
          )}
          {Array.isArray(g.avoid_trade_zone) && g.avoid_trade_zone.length > 0 && (
            <span>
              Reversal / caution zone: <strong>{g.avoid_trade_zone.join(" · ")}</strong>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
