"use client";

import type { LongTermIdea } from "@/lib/api";

interface Props {
  items: LongTermIdea[];
  slotInfo?: string;
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(2);
}

function dataBadge(auth: string) {
  if (auth === "real") return { label: "Verified", color: "#00c853" };
  if (auth === "partial") return { label: "Partial", color: "#ff9800" };
  return { label: "Estimated", color: "#ff5252" };
}

function gapColor(gap: number | null | undefined): string {
  if (gap === null || gap === undefined) return "var(--text-secondary)";
  const abs = Math.abs(gap);
  if (abs <= 2) return "#00c853";
  if (abs <= 5) return "#ff9800";
  return "#ff5252";
}

function actionBadge(tag: string | undefined) {
  if (tag === "EXECUTE_NOW") return { label: "Execute Now", bg: "#00c85322", color: "#00c853", border: "#00c85344" };
  if (tag === "WAIT_FOR_RETEST") return { label: "Wait for Retest", bg: "#ff980022", color: "#ff9800", border: "#ff980044" };
  if (tag === "MISSED") return { label: "Missed", bg: "#ff525222", color: "#ff5252", border: "#ff525244" };
  return null;
}

function fmtDate(d: string | null | undefined) {
  if (!d) return "-";
  return String(d).slice(0, 10);
}

export function LongTermIdeasCard({ items, slotInfo }: Props) {
  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Long-Term Investment Ideas</span>
        {slotInfo && <span style={{ fontSize: "0.75rem", color: "var(--accent)", fontWeight: 500 }}>{slotInfo}</span>}
      </div>
      {items.length === 0 ? (
        <div style={{ color: "var(--text-secondary)", padding: "24px 0", textAlign: "center" }}>
          <div style={{ fontSize: "1.1rem", marginBottom: 8 }}>No high-quality long-term opportunities found</div>
          <div style={{ fontSize: "0.82rem", color: "var(--text-dim)" }}>
            The weekly SMC analysis found no stocks meeting our quality bar. This means the system is working correctly &mdash; only genuine setups with confirmed weekly structure, OB/FVG zones, and institutional volume will appear here.
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
          {items.map(item => {
            const badge = dataBadge(item.data_authenticity);
            return (
              <div key={item.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "rgba(255,255,255,0.01)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <strong>{item.symbol}</strong>
                    <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: badge.color + "22", color: badge.color, border: `1px solid ${badge.color}44` }}>{badge.label}</span>
                    {item.entry_type && (
                      <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: item.entry_type === "LIMIT" ? "#ff980022" : "#00c85322", color: item.entry_type === "LIMIT" ? "#ff9800" : "#00c853", border: `1px solid ${item.entry_type === "LIMIT" ? "#ff980044" : "#00c85344"}` }}>{item.entry_type}</span>
                    )}
                    {(() => { const ab = actionBadge(item.action_tag); return ab ? <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: ab.bg, color: ab.color, border: `1px solid ${ab.border}` }}>{ab.label}</span> : null; })()}
                  </div>
                  <span style={{ color: "#00d4ff", fontSize: "0.78rem" }}>{item.confidence_score.toFixed(1)}%</span>
                </div>
                {item.setup && (
                  <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginBottom: 4 }}>{item.setup}</div>
                )}
                <div style={{ color: "var(--text-secondary)", fontSize: "0.8rem", marginBottom: 8 }}>{item.reasoning_summary || item.long_term_thesis}</div>
                <div style={{ fontSize: "0.78rem", display: "grid", gap: 4 }}>
                  {item.scan_cmp != null && (
                    <div><span style={{ color: "var(--text-secondary)" }}>CMP:</span> {fmt(item.scan_cmp)}
                      {item.entry_gap_pct != null && (
                        <span style={{ marginLeft: 8, color: gapColor(item.entry_gap_pct), fontWeight: 600 }}>
                          ({item.entry_gap_pct > 0 ? "+" : ""}{item.entry_gap_pct.toFixed(1)}%)
                        </span>
                      )}
                    </div>
                  )}
                  <div><span style={{ color: "var(--text-secondary)" }}>Entry:</span> {fmt(item.entry_price)} | <span style={{ color: "var(--text-secondary)" }}>SL:</span> {fmt(item.stop_loss)}</div>
                  <div><span style={{ color: "var(--text-secondary)" }}>Entry Zone:</span> {Array.isArray(item.entry_zone) && item.entry_zone.length === 2 ? `${fmt(item.entry_zone[0])} - ${fmt(item.entry_zone[1])}` : "-"}</div>
                  <div><span style={{ color: "var(--text-secondary)" }}>Target:</span> {fmt(item.long_term_target)} | <span style={{ color: "var(--text-secondary)" }}>R:R:</span> {item.risk_reward ? item.risk_reward.toFixed(1) : "-"}</div>
                  <div><span style={{ color: "var(--text-secondary)" }}>Fair Value:</span> {fmt(item.fair_value_estimate)}</div>
                  <div><span style={{ color: "var(--text-secondary)" }}>Risk Factors:</span> {item.risk_factors.join(", ") || "-"}</div>
                  <div style={{ color: "var(--text-dim)", fontSize: "0.7rem", marginTop: 4 }}>Signal detected: {fmtDate(item.signal_first_detected_at)} | Updated: {fmtDate(item.signals_updated_at)}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
