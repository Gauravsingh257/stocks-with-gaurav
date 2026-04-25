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

export function FinalTrades({ items }: { items: ResearchDecisionCard[] }) {
  const display = items.slice(0, 6);

  return (
    <section className="glass" style={{ padding: 16, display: "grid", gap: 12, border: "1px solid rgba(0,224,150,0.22)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>🔥 Final Trade Ideas</h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Fully validated setups ready for execution</p>
        </div>
        <span style={{ fontSize: "0.68rem", padding: "3px 8px", borderRadius: 6, background: "rgba(0,224,150,0.1)", border: "1px solid rgba(0,224,150,0.28)", color: "#00e096", fontWeight: 800 }}>
          {display.length} Ready
        </span>
      </div>

      {display.length === 0 ? (
        <div style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-secondary)", fontSize: "0.82rem", background: "rgba(255,255,255,0.02)" }}>
          No fully confirmed setup is ready right now.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
          {display.map((item) => {
            const symbol = cleanSymbol(item.symbol);
            const target = targetOf(item);
            return (
              <article key={item.symbol} style={{ border: "1px solid rgba(0,224,150,0.2)", borderRadius: 8, padding: 12, background: "rgba(0,224,150,0.04)", display: "grid", gap: 10 }}>
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
                    <ShieldCheck size={12} /> Strong Buy
                  </span>
                  <span style={{ fontSize: "0.66rem", padding: "3px 7px", borderRadius: 6, color: "#f0c060", background: "rgba(240,192,96,0.12)", border: "1px solid rgba(240,192,96,0.28)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <Activity size={12} /> Execute Now
                  </span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, fontSize: "0.74rem" }}>
                  <div><span style={{ color: "var(--text-dim)" }}>Entry</span><br /><strong>{fmt(item.entry_price)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>SL</span><br /><strong style={{ color: "#ff4e6a" }}>{fmt(item.stop_loss)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>Target</span><br /><strong style={{ color: "#00e096" }}>{fmt(target)}</strong></div>
                </div>
                <Link href={`/research/chart?symbol=${encodeURIComponent(symbol)}&horizon=SWING`} style={{ color: "#5b9cf6", textDecoration: "none", fontSize: "0.72rem", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <Target size={12} /> Open chart validation
                </Link>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}