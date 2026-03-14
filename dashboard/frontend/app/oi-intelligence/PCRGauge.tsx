"use client";
import { HexPattern } from "@/components/FuturisticElements";
import { fmt, pcrZone, trendArrow } from "./types";

export function PCRGauge({ pcr, trend, confidence }: { pcr: number; trend: string; confidence: number }) {
  const clamped = Math.min(Math.max(pcr, 0), 2);
  const angle = (clamped / 2) * 180;
  const zone = pcrZone(pcr);

  return (
    <div className="glass-glow" style={{ padding: 24, textAlign: "center", position: "relative", overflow: "hidden" }}>
      <HexPattern style={{ opacity: 0.03 }} />
      <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 16, fontWeight: 600 }}>
        PCR GAUGE
      </div>

      <div style={{ position: "relative", width: 220, height: 130, margin: "0 auto" }}>
        <svg viewBox="0 0 220 130" width="220" height="130">
          <path d="M 20 120 A 90 90 0 0 1 200 120" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="14" strokeLinecap="round" />
          <path d="M 20 120 A 90 90 0 0 1 47.2 50.3" fill="none" stroke="rgba(255,71,87,0.3)" strokeWidth="14" strokeLinecap="round" />
          <path d="M 47.2 50.3 A 90 90 0 0 1 138.5 32.1" fill="none" stroke="rgba(255,165,2,0.3)" strokeWidth="14" />
          <path d="M 138.5 32.1 A 90 90 0 0 1 200 120" fill="none" stroke="rgba(0,224,150,0.3)" strokeWidth="14" strokeLinecap="round" />
          <line
            x1="110" y1="120"
            x2={110 + 75 * Math.cos((Math.PI * (180 - angle)) / 180)}
            y2={120 - 75 * Math.sin((Math.PI * (180 - angle)) / 180)}
            stroke={zone.color} strokeWidth="3" strokeLinecap="round"
            style={{ filter: `drop-shadow(0 0 6px ${zone.color})`, transition: "all 0.8s ease" }}
          />
          <circle cx="110" cy="120" r="6" fill={zone.color} style={{ filter: `drop-shadow(0 0 8px ${zone.color})` }} />
          <text x="20" y="128" fill="var(--text-dim)" fontSize="9" textAnchor="middle">0.0</text>
          <text x="110" y="22" fill="var(--text-dim)" fontSize="9" textAnchor="middle">1.0</text>
          <text x="200" y="128" fill="var(--text-dim)" fontSize="9" textAnchor="middle">2.0</text>
        </svg>
      </div>

      <div style={{ fontSize: "2rem", fontWeight: 800, color: zone.color, lineHeight: 1, marginTop: 4, fontFamily: "monospace" }}>
        {fmt(pcr, 3)}
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 6 }}>
        <span className={`badge ${zone.label === "BULLISH" ? "badge-win" : zone.label === "BEARISH" ? "badge-loss" : "badge-neutral"}`}>
          {zone.label}
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 3, fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {trendArrow(trend)} {trend}
        </span>
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.65rem", color: "var(--text-dim)", marginBottom: 4 }}>
          <span>CONFIDENCE</span>
          <span style={{ color: confidence >= 70 ? "var(--success)" : confidence >= 40 ? "var(--warning)" : "var(--text-dim)" }}>{confidence}%</span>
        </div>
        <div style={{ height: 4, borderRadius: 2, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
          <div style={{
            height: "100%", width: `${confidence}%`, borderRadius: 2,
            background: confidence >= 70 ? "var(--success)" : confidence >= 40 ? "var(--warning)" : "var(--muted)",
            transition: "width 0.8s ease",
            boxShadow: confidence >= 70 ? "0 0 8px rgba(0,224,150,0.4)" : undefined,
          }} />
        </div>
      </div>
    </div>
  );
}
