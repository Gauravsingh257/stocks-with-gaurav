"use client";

import Link from "next/link";
import { Bell, ExternalLink, Eye, TimerReset } from "lucide-react";
import type { ResearchDecisionCard } from "@/lib/api";

function cleanSymbol(symbol: string): string {
  return symbol.replace(/^NSE:/i, "").replace(/\.NS$/i, "");
}

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}

function watchReason(item: ResearchDecisionCard): string {
  const signals = item.technical_signals || {};
  const detail = signals.ob_liquidity || signals.ob_fvg || signals.structure || signals.daily_structure;
  if (detail) return `Reason: ${detail}`;
  if (item.reasoning) return `Reason: ${item.reasoning.split(".")[0]}`;
  return `Reason: ${item.setup || "SMC score is near the execution zone"}`;
}

function confidenceText(item: ResearchDecisionCard): string {
  const score = Number(item.confidence_score || 0);
  if (item.near_setup) return "Confidence: Near setup. One more confirmation can move it to Final.";
  if (score >= 50) return "Confidence: Moderate. Setup quality is present, entry confirmation is still pending.";
  return "Confidence: Early watchlist quality. Wait for price action confirmation.";
}

function riskNote(item: ResearchDecisionCard): string {
  return `Risk note: Alert near entry ${fmt(item.entry_price)} and invalidate below SL ${fmt(item.stop_loss)}.`;
}

export function Watchlist({ items }: { items: ResearchDecisionCard[] }) {
  return (
    <section className="glass border-yellow-400 bg-yellow-500/5" style={{ padding: 16, display: "grid", gap: 12, border: "1px solid #facc15", background: "rgba(234,179,8,0.05)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>🟡 Watchlist (Near Entry)</h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Almost ready setups waiting for entry confirmation</p>
        </div>
        <span style={{ fontSize: "0.68rem", padding: "3px 8px", borderRadius: 6, background: "rgba(234,179,8,0.12)", border: "1px solid rgba(250,204,21,0.42)", color: "#facc15", fontWeight: 850 }}>
          🟡 Monitor · {items.length}
        </span>
      </div>

      {items.length === 0 ? (
        <div style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-secondary)", fontSize: "0.82rem", background: "rgba(255,255,255,0.02)" }}>
          No near setup is waiting for confirmation.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 }}>
          {items.map((item) => {
            const symbol = cleanSymbol(item.symbol);
            return (
              <article className="border-yellow-400 bg-yellow-500/5" key={item.symbol} style={{ border: "1px solid #facc15", borderRadius: 8, padding: 12, background: "rgba(234,179,8,0.05)", display: "grid", gap: 9 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 820, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    {symbol} <ExternalLink size={12} />
                  </Link>
                  <span style={{ color: "#f59e0b", fontSize: "0.82rem", fontWeight: 850 }}>{Number(item.confidence_score || 0).toFixed(1)}%</span>
                </div>
                <div style={{ color: "var(--text-dim)", fontSize: "0.68rem" }}>{item.setup || "Quality passed, SMC pending"}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#facc15", background: "rgba(234,179,8,0.12)", border: "1px solid rgba(250,204,21,0.34)", fontWeight: 850, display: "inline-flex", alignItems: "center", gap: 4 }}><TimerReset size={12} /> 🟡 Monitor</span>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#5b9cf6", background: "rgba(91,156,246,0.1)", border: "1px solid rgba(91,156,246,0.24)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}><Eye size={12} /> Near OB</span>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#b07cf0", background: "rgba(176,124,240,0.1)", border: "1px solid rgba(176,124,240,0.24)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}><Bell size={12} /> Monitoring</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: "0.74rem", color: "var(--text-secondary)" }}>
                  <span>CMP <strong style={{ color: "var(--text-primary)" }}>{fmt(item.scan_cmp)}</strong></span>
                  <span>Entry <strong style={{ color: "var(--text-primary)" }}>{fmt(item.entry_price)}</strong></span>
                  <span>R:R <strong style={{ color: "var(--text-primary)" }}>{fmt(item.risk_reward)}</strong></span>
                </div>

                <div style={{ display: "grid", gap: 6, padding: 9, borderRadius: 8, background: "rgba(15,23,42,0.26)", border: "1px solid rgba(250,204,21,0.2)", fontSize: "0.71rem", lineHeight: 1.45 }}>
                  <div style={{ color: "var(--text-primary)", fontWeight: 850 }}>Why this trade?</div>
                  <div style={{ color: "var(--text-secondary)" }}>{watchReason(item)}</div>
                  <div style={{ color: "#fde68a" }}>{confidenceText(item)}</div>
                  <div style={{ color: "#fecaca" }}>{riskNote(item)}</div>
                </div>

                <Link href={`/research/chart?symbol=${encodeURIComponent(symbol)}&horizon=SWING`} style={{ color: "#1f1600", background: "#facc15", border: "1px solid rgba(250,204,21,0.65)", borderRadius: 7, padding: "7px 10px", textDecoration: "none", fontSize: "0.72rem", fontWeight: 900, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
                  <Bell size={13} /> Add Alert
                </Link>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}