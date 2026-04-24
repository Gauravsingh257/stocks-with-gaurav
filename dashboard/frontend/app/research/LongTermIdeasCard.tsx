"use client";

import { useState } from "react";
import type { LongTermIdea } from "@/lib/api";
import { CmpFreshnessBadge } from "./CmpFreshnessBadge";
import { SmcEvidencePanel } from "./SmcEvidencePanel";

interface Props {
  items: LongTermIdea[];
  slotInfo?: string;
  onScan?: () => void;
  scanning?: boolean;
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
  if (tag === "IN_MOTION") return { label: "In Progress", bg: "#7ea8ff22", color: "#7ea8ff", border: "#7ea8ff44" };
  if (tag === "MISSED") return { label: "Missed", bg: "#ff525222", color: "#ff5252", border: "#ff525244" };
  return null;
}

function fmtDate(d: string | null | undefined) {
  if (!d) return "-";
  try {
    const s = String(d).replace(" ", "T");
    const norm = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z";
    const dt = new Date(norm);
    return dt.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return String(d).slice(0, 10);
  }
}

function LongTermCard({ item }: { item: LongTermIdea }) {
  const [showReasoning, setShowReasoning] = useState(false);
  const badge = dataBadge(item.data_authenticity);
  const reasoning = item.reasoning_summary || item.long_term_thesis;
  const hasEntryZone = Array.isArray(item.entry_zone) && item.entry_zone.length === 2;
  const hasRiskFactors = item.risk_factors && item.risk_factors.length > 0
    && !item.risk_factors.every(r => ["Earnings miss risk", "Macro sentiment reversal", "Liquidity contraction", "Sector rotation reversal", "Macro policy volatility"].includes(r));

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12, background: "rgba(255,255,255,0.01)", transition: "border-color 0.3s, transform 0.3s, box-shadow 0.3s" }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = "rgba(34,211,238,0.3)"; e.currentTarget.style.transform = "translateY(-3px)"; e.currentTarget.style.boxShadow = "0 12px 40px rgba(0,212,255,0.08)"; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.transform = "none"; e.currentTarget.style.boxShadow = "none"; }}
    >
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <strong>{item.symbol}</strong>
          {item.sector ? (
            <span style={{
              fontSize: "0.6rem", padding: "1px 6px", borderRadius: 4,
              background: "rgba(255,255,255,0.04)", color: "var(--text-secondary)",
              border: "1px solid var(--border-muted)", letterSpacing: 0.3,
              textTransform: "uppercase", fontWeight: 500,
            }}>
              {item.sector}
            </span>
          ) : null}
          <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: badge.color + "22", color: badge.color, border: `1px solid ${badge.color}44` }}>{badge.label}</span>
          {item.entry_type && (
            <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: item.entry_type === "LIMIT" ? "#ff980022" : "#00c85322", color: item.entry_type === "LIMIT" ? "#ff9800" : "#00c853", border: `1px solid ${item.entry_type === "LIMIT" ? "#ff980044" : "#00c85344"}` }}>{item.entry_type}</span>
          )}
          {(() => { const ab = actionBadge(item.action_tag); return ab ? <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 4, background: ab.bg, color: ab.color, border: `1px solid ${ab.border}` }}>{ab.label}</span> : null; })()}
        </div>
        <span style={{ color: "#00d4ff", fontSize: "0.78rem", fontWeight: 600 }}>{item.confidence_score.toFixed(1)}%</span>
      </div>

      {item.setup && (
        <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginBottom: 6 }}>{item.setup}</div>
      )}

      {/* Reasoning — collapsed by default */}
      {reasoning && (
        <div style={{ marginBottom: 8 }}>
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            style={{
              background: "rgba(41, 98, 255, 0.08)", border: "1px solid rgba(41, 98, 255, 0.2)",
              borderRadius: 5, padding: "3px 10px", cursor: "pointer",
              color: "#5b9cf6", fontSize: "0.72rem", fontWeight: 500,
              display: "inline-flex", alignItems: "center", gap: 4,
              transition: "background 0.2s",
            }}
          >
            <span style={{ transform: showReasoning ? "rotate(90deg)" : "none", transition: "transform 0.2s", fontSize: "0.7rem" }}>▶</span>
            {showReasoning ? "Hide Analysis" : "View Analysis"}
          </button>
          <a
            href={`/research/chart?symbol=${encodeURIComponent(item.symbol.replace("NSE:", ""))}&horizon=LONGTERM`}
            style={{
              background: "rgba(0, 209, 140, 0.08)", border: "1px solid rgba(0, 209, 140, 0.25)",
              borderRadius: 5, padding: "3px 10px", cursor: "pointer",
              color: "#00d18c", fontSize: "0.72rem", fontWeight: 500,
              display: "inline-flex", alignItems: "center", gap: 4,
              textDecoration: "none", transition: "background 0.2s",
            }}
          >
            📊 Chart
          </a>
          {showReasoning && (
            <div style={{ color: "var(--text-secondary)", fontSize: "0.78rem", marginTop: 6, padding: "8px 10px", background: "rgba(41, 98, 255, 0.04)", borderRadius: 6, borderLeft: "2px solid rgba(41, 98, 255, 0.3)", lineHeight: 1.5 }}>
              {reasoning}
            </div>
          )}
          {showReasoning && (
            <div style={{ marginTop: 8 }}>
              <SmcEvidencePanel evidence={item.smc_evidence} />
            </div>
          )}
        </div>
      )}

      {/* Key metrics */}
      <div style={{ fontSize: "0.78rem", display: "grid", gap: 4 }}>
        {item.scan_cmp != null && (
          <div><span style={{ color: "var(--text-secondary)" }}>CMP:</span> <strong>{fmt(item.scan_cmp)}</strong>
            <CmpFreshnessBadge source={item.cmp_source} ageSec={item.cmp_age_sec} />
            {item.entry_gap_pct != null && (
              <span style={{ marginLeft: 8, color: gapColor(item.entry_gap_pct), fontWeight: 600 }}>
                ({item.entry_gap_pct > 0 ? "+" : ""}{item.entry_gap_pct.toFixed(1)}%)
              </span>
            )}
          </div>
        )}
        <div>
          <span style={{ color: "var(--text-secondary)" }}>Entry:</span> <strong>{fmt(item.entry_price)}</strong>
          {hasEntryZone && (
            <span style={{ color: "var(--text-dim)", marginLeft: 6, fontSize: "0.74rem" }}>
              (Zone: {fmt(item.entry_zone![0])} – {fmt(item.entry_zone![1])})
            </span>
          )}
          {" | "}<span style={{ color: "var(--text-secondary)" }}>SL:</span> <span style={{ color: "#ff4e6a" }}>{fmt(item.stop_loss)}</span>
        </div>
        <div>
          <span style={{ color: "var(--text-secondary)" }}>Target:</span> <span style={{ color: "#00d18c" }}>{fmt(item.long_term_target)}</span>
          {" | "}<span style={{ color: "var(--text-secondary)" }}>R:R:</span> {item.risk_reward ? item.risk_reward.toFixed(1) : "-"}
          {item.fair_value_estimate != null && (
            <span style={{ marginLeft: 8 }}><span style={{ color: "var(--text-secondary)" }}>FV:</span> {fmt(item.fair_value_estimate)}</span>
          )}
        </div>
        {(item.pe_ratio != null || item.roe_pct != null || item.market_cap_cr != null || item.debt_equity != null) && (
          <div style={{ fontSize: "0.74rem", display: "flex", gap: 12, flexWrap: "wrap" }}>
            {item.pe_ratio != null && (
              <span><span style={{ color: "var(--text-secondary)" }}>PE:</span>{" "}
                <strong style={{ color: item.pe_ratio > 50 ? "#ff4e6a" : item.pe_ratio > 30 ? "#f0c060" : "#00d18c" }}>{item.pe_ratio.toFixed(1)}</strong>
              </span>
            )}
            {item.roe_pct != null && (
              <span><span style={{ color: "var(--text-secondary)" }}>ROE:</span>{" "}
                <strong style={{ color: item.roe_pct >= 15 ? "#00d18c" : item.roe_pct >= 8 ? "#f0c060" : "#ff4e6a" }}>{item.roe_pct.toFixed(1)}%</strong>
              </span>
            )}
            {item.market_cap_cr != null && (
              <span><span style={{ color: "var(--text-secondary)" }}>MCap:</span>{" "}
                <strong>{item.market_cap_cr >= 10000 ? `${(item.market_cap_cr / 10000).toFixed(1)}L Cr` : `${Math.round(item.market_cap_cr)} Cr`}</strong>
              </span>
            )}
            {item.debt_equity != null && (
              <span><span style={{ color: "var(--text-secondary)" }}>D/E:</span>{" "}
                <strong style={{ color: item.debt_equity > 1.5 ? "#ff4e6a" : item.debt_equity > 0.5 ? "#f0c060" : "#00d18c" }}>{item.debt_equity.toFixed(2)}</strong>
              </span>
            )}
          </div>
        )}
        {hasRiskFactors && (
          <div style={{ fontSize: "0.74rem" }}><span style={{ color: "var(--text-secondary)" }}>Risks:</span> {item.risk_factors.join(", ")}</div>
        )}
        <div style={{ color: "var(--text-dim)", fontSize: "0.7rem", marginTop: 4 }}>
          Detected: {fmtDate(item.signal_first_detected_at)} | Updated: {fmtDate(item.signals_updated_at)}
        </div>
      </div>
    </div>
  );
}

export function LongTermIdeasCard({ items, slotInfo, onScan, scanning }: Props) {
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
            The weekly SMC analysis found no stocks meeting our quality bar. Only genuine setups with confirmed weekly structure, OB/FVG zones, and institutional volume will appear here.
          </div>
          {onScan && (
            <button
              onClick={onScan}
              disabled={scanning}
              style={{
                marginTop: 12, padding: "6px 16px", borderRadius: 8, fontWeight: 600,
                fontSize: "0.75rem", cursor: scanning ? "wait" : "pointer",
                background: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.3)",
                color: "#f59e0b", opacity: scanning ? 0.6 : 1,
              }}
            >
              {scanning ? "Scanning..." : "Run Long-Term Scan"}
            </button>
          )}
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
          {items.map(item => <LongTermCard key={item.id} item={item} />)}
        </div>
      )}
    </div>
  );
}
