"use client";

import Link from "next/link";
import { Activity, ExternalLink, ShieldCheck, Target } from "lucide-react";
import type { ResearchDecisionCard } from "@/lib/api";

function cleanSymbol(symbol: string): string {
  return symbol.replace(/^NSE:/i, "").replace(/\.NS$/i, "");
}

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}

function targetOf(item: ResearchDecisionCard): number | null {
  if (item.target_2 !== undefined && item.target_2 !== null) return item.target_2;
  if (item.target_1 !== undefined && item.target_1 !== null) return item.target_1;
  if (Array.isArray(item.targets) && item.targets.length > 0) return item.targets[item.targets.length - 1];
  return null;
}

function shortReason(item: ResearchDecisionCard): string {
  const signals = item.technical_signals || {};
  const evidence = [signals.daily_structure, signals.structure, signals.ob_fvg, signals.ob_liquidity]
    .filter(Boolean)
    .slice(0, 2)
    .join(" + ");
  if (evidence) return `Reason: ${evidence}`;
  if (item.reasoning) return `Reason: ${item.reasoning.split(".")[0]}`;
  return `Reason: ${item.setup || "SMC confirmation and risk levels are aligned"}`;
}

function confidenceText(item: ResearchDecisionCard): string {
  const score = Number(item.confidence_score || 0);
  if (score >= 70) return "Confidence: Strong because SMC, quality, and execution layers are aligned.";
  if (score >= 60) return "Confidence: Good because the setup passed final SMC scoring with defined levels.";
  return "Confidence: Actionable, but confirm the chart before execution.";
}

function riskNote(item: ResearchDecisionCard, target: number | null): string {
  const rr = item.risk_reward ? `R:R ${fmt(item.risk_reward)}` : target ? "target mapped" : "target pending";
  return `Risk note: Use SL ${fmt(item.stop_loss)}. ${rr}. Avoid entry if price is far from the planned zone.`;
}

export function FinalTrades({ items }: { items: ResearchDecisionCard[] }) {
  const display = items.slice(0, 6);

  return (
    <section className="glass border-emerald-500 shadow-xl" style={{ padding: 18, display: "grid", gap: 14, border: "1px solid #10b981", boxShadow: "0 24px 60px rgba(16,185,129,0.16), 0 0 28px rgba(16,185,129,0.14)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>🔥 Final Trade Ideas</h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Only high-conviction setups cleared for action</p>
        </div>
        <span style={{ fontSize: "0.72rem", padding: "4px 10px", borderRadius: 6, background: "rgba(16,185,129,0.14)", border: "1px solid rgba(16,185,129,0.5)", color: "#34d399", fontWeight: 900, boxShadow: "0 0 18px rgba(16,185,129,0.18)" }}>
          🔥 Execute Now · {display.length}
        </span>
      </div>

      {display.length === 0 ? (
        <div style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-secondary)", fontSize: "0.82rem", background: "rgba(255,255,255,0.02)" }}>
          No fully confirmed setup is ready right now.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 14, padding: "4px 2px" }}>
          {display.map((item) => {
            const symbol = cleanSymbol(item.symbol);
            const target = targetOf(item);
            return (
              <article className="scale-105 border-emerald-500 shadow-xl" key={item.symbol} style={{ border: "1px solid #10b981", borderRadius: 8, padding: 14, background: "linear-gradient(180deg, rgba(16,185,129,0.12), rgba(16,185,129,0.045))", display: "grid", gap: 11, transform: "scale(1.03)", transformOrigin: "center", boxShadow: "0 18px 42px rgba(16,185,129,0.18), 0 0 24px rgba(16,185,129,0.16)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}>
                  <div>
                    <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 850, display: "inline-flex", alignItems: "center", gap: 5 }}>
                      {symbol} <ExternalLink size={12} />
                    </Link>
                    <div style={{ color: "var(--text-dim)", fontSize: "0.68rem", marginTop: 2 }}>{item.setup || "SMC confirmed"}</div>
                  </div>
                  <div style={{ color: "#00e096", fontWeight: 850, fontSize: "0.86rem" }}>{Number(item.confidence_score || 0).toFixed(1)}%</div>
                </div>

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.66rem", padding: "3px 7px", borderRadius: 6, color: "#00e096", background: "rgba(0,224,150,0.12)", border: "1px solid rgba(0,224,150,0.28)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <ShieldCheck size={12} /> High Conviction
                  </span>
                  <span style={{ fontSize: "0.68rem", padding: "3px 8px", borderRadius: 6, color: "#34d399", background: "rgba(16,185,129,0.16)", border: "1px solid rgba(16,185,129,0.4)", fontWeight: 900, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <Activity size={12} /> 🔥 Execute Now
                  </span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, fontSize: "0.74rem" }}>
                  <div><span style={{ color: "var(--text-dim)" }}>Entry</span><br /><strong>{fmt(item.entry_price)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>SL</span><br /><strong style={{ color: "#ff4e6a" }}>{fmt(item.stop_loss)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>Target</span><br /><strong style={{ color: "#00e096" }}>{fmt(target)}</strong></div>
                </div>

                <div style={{ display: "grid", gap: 6, padding: 10, borderRadius: 8, background: "rgba(3,7,18,0.28)", border: "1px solid rgba(16,185,129,0.22)", fontSize: "0.72rem", lineHeight: 1.45 }}>
                  <div style={{ color: "var(--text-primary)", fontWeight: 850 }}>Why this trade?</div>
                  <div style={{ color: "var(--text-secondary)" }}>{shortReason(item)}</div>
                  <div style={{ color: "#a7f3d0" }}>{confidenceText(item)}</div>
                  <div style={{ color: "#fca5a5" }}>{riskNote(item, target)}</div>
                </div>

                <Link href={`/research/chart?symbol=${encodeURIComponent(symbol)}&horizon=SWING`} style={{ color: "#04130d", background: "#34d399", border: "1px solid rgba(16,185,129,0.7)", borderRadius: 7, padding: "8px 10px", textDecoration: "none", fontSize: "0.74rem", fontWeight: 900, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
                  <Target size={13} /> Execute Trade
                </Link>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}