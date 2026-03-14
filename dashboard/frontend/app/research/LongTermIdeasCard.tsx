"use client";

import type { LongTermIdea } from "@/lib/api";

interface Props {
  items: LongTermIdea[];
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(2);
}

export function LongTermIdeasCard({ items }: Props) {
  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Long-Term Investment Ideas</div>
      {items.length === 0 ? (
        <div style={{ color: "var(--text-secondary)" }}>No long-term ideas yet. Run the monthly scan or wait for scheduler.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {items.map(item => (
            <div key={item.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "rgba(255,255,255,0.01)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <strong>{item.symbol}</strong>
                <span style={{ color: "#00d4ff", fontSize: "0.78rem" }}>{item.confidence_score.toFixed(1)}%</span>
              </div>
              <div style={{ color: "var(--text-secondary)", fontSize: "0.8rem", marginBottom: 8 }}>{item.long_term_thesis}</div>
              <div style={{ fontSize: "0.78rem", display: "grid", gap: 4 }}>
                <div><span style={{ color: "var(--text-secondary)" }}>Entry Zone:</span> {Array.isArray(item.entry_zone) && item.entry_zone.length === 2 ? `${fmt(item.entry_zone[0])} - ${fmt(item.entry_zone[1])}` : "-"}</div>
                <div><span style={{ color: "var(--text-secondary)" }}>Target:</span> {fmt(item.long_term_target)}</div>
                <div><span style={{ color: "var(--text-secondary)" }}>Fair Value:</span> {fmt(item.fair_value_estimate)}</div>
                <div><span style={{ color: "var(--text-secondary)" }}>Risk Factors:</span> {item.risk_factors.join(", ") || "-"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
