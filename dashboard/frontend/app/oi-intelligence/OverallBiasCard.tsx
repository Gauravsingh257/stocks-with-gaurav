"use client";
import { Zap } from "lucide-react";
import { HexPattern } from "@/components/FuturisticElements";
import { biasColor, biasIcon, type OISnapshot } from "./types";

export function OverallBiasCard({ snapshot }: { snapshot: OISnapshot }) {
  const { overall_bias, confidence, high_conviction, bull_score, bear_score, market_open } = snapshot;
  const bc = biasColor(overall_bias);
  return (
    <div className="glass-glow" style={{ padding: 24, position: "relative", overflow: "hidden" }}>
      <HexPattern style={{ opacity: 0.03 }} />
      <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 12, fontWeight: 600 }}>
        OVERALL OI BIAS
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{
          width: 56, height: 56, borderRadius: "50%",
          background: `${bc}15`, border: `2px solid ${bc}`,
          display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: `0 0 20px ${bc}33`,
          animation: high_conviction ? "glow-pulse 2s ease-in-out infinite" : undefined,
        }}>
          {biasIcon(overall_bias)}
        </div>
        <div>
          <div style={{ fontSize: "1.5rem", fontWeight: 800, color: bc, letterSpacing: "0.03em" }}>
            {overall_bias || "N/A"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
            {high_conviction && (
              <span className="badge badge-win" style={{ fontSize: "0.6rem" }}>
                <Zap size={10} /> HIGH CONVICTION
              </span>
            )}
            <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
              {market_open ? "● LIVE" : "○ CLOSED"}
            </span>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.65rem", color: "var(--text-dim)", marginBottom: 4 }}>
          <span style={{ color: "var(--success)" }}>BULL {bull_score}</span>
          <span style={{ color: "var(--danger)" }}>BEAR {bear_score}</span>
        </div>
        <div style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", gap: 2 }}>
          <div style={{
            flex: bull_score || 1, background: "var(--success)",
            borderRadius: "3px 0 0 3px", boxShadow: "0 0 8px rgba(0,224,150,0.3)", transition: "flex 0.8s ease",
          }} />
          <div style={{
            flex: bear_score || 1, background: "var(--danger)",
            borderRadius: "0 3px 3px 0", boxShadow: "0 0 8px rgba(255,71,87,0.3)", transition: "flex 0.8s ease",
          }} />
        </div>
      </div>
    </div>
  );
}
