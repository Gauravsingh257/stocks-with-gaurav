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

export function Watchlist({ items }: { items: ResearchDecisionCard[] }) {
  return (
    <section className="glass" style={{ padding: 16, display: "grid", gap: 12, border: "1px solid rgba(245,158,11,0.2)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)" }}>🟡 Watchlist (Near Entry)</h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Almost ready setups waiting for entry confirmation</p>
        </div>
        <span style={{ fontSize: "0.68rem", padding: "3px 8px", borderRadius: 6, background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.26)", color: "#f59e0b", fontWeight: 800 }}>
          Wait / Monitor · {items.length}
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
              <article key={item.symbol} style={{ border: "1px solid rgba(245,158,11,0.2)", borderRadius: 8, padding: 12, background: "rgba(245,158,11,0.04)", display: "grid", gap: 9 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 820, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    {symbol} <ExternalLink size={12} />
                  </Link>
                  <span style={{ color: "#f59e0b", fontSize: "0.82rem", fontWeight: 850 }}>{Number(item.confidence_score || 0).toFixed(1)}%</span>
                </div>
                <div style={{ color: "var(--text-dim)", fontSize: "0.68rem" }}>{item.setup || "Quality passed, SMC pending"}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#f59e0b", background: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.26)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}><TimerReset size={12} /> Wait / Monitor</span>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#5b9cf6", background: "rgba(91,156,246,0.1)", border: "1px solid rgba(91,156,246,0.24)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}><Eye size={12} /> Near OB</span>
                  <span style={{ fontSize: "0.65rem", padding: "3px 7px", borderRadius: 6, color: "#b07cf0", background: "rgba(176,124,240,0.1)", border: "1px solid rgba(176,124,240,0.24)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}><Bell size={12} /> Monitoring</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: "0.74rem", color: "var(--text-secondary)" }}>
                  <span>CMP <strong style={{ color: "var(--text-primary)" }}>{fmt(item.scan_cmp)}</strong></span>
                  <span>Entry <strong style={{ color: "var(--text-primary)" }}>{fmt(item.entry_price)}</strong></span>
                  <span>R:R <strong style={{ color: "var(--text-primary)" }}>{fmt(item.risk_reward)}</strong></span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}