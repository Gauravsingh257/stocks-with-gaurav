"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ExternalLink, Search } from "lucide-react";
import type { ResearchDecisionCard } from "@/lib/api";

function cleanSymbol(symbol: string): string {
  return symbol.replace(/^NSE:/i, "").replace(/\.NS$/i, "");
}

function reasons(item: ResearchDecisionCard): string {
  const raw = item.rejection_reason || [];
  return raw.length ? raw.slice(0, 3).join(" · ") : "early-stage candidate";
}

export function DiscoveryFeed({ items }: { items: ResearchDecisionCard[] }) {
  const [open, setOpen] = useState(false);

  return (
    <section className="glass opacity-70" style={{ padding: 14, display: "grid", gap: 10, opacity: 0.7 }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, width: "100%", background: "transparent", border: 0, padding: 0, color: "inherit", cursor: "pointer", textAlign: "left" }}
      >
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
            {open ? "🔍 Early Signals (Click to Collapse)" : "🔍 Early Signals (Click to Expand)"}
          </h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Low-confidence candidates for exploration only</p>
        </div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.66rem", padding: "3px 8px", borderRadius: 6, background: "rgba(91,156,246,0.08)", border: "1px solid rgba(91,156,246,0.2)", color: "#93c5fd", fontWeight: 800 }}>
          🔍 Early Signal · {items.length} <ChevronDown size={14} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />
        </span>
      </button>

      {open && (
        items.length === 0 ? (
          <div style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-secondary)", fontSize: "0.82rem", background: "rgba(255,255,255,0.02)" }}>
            No early experimental signals in the latest scan.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 6 }}>
            {items.map((item) => {
              const symbol = cleanSymbol(item.symbol);
              return (
                <article key={item.symbol} style={{ display: "grid", gridTemplateColumns: "minmax(110px, 0.75fr) minmax(150px, 1fr) auto", gap: 8, alignItems: "center", border: "1px solid var(--border)", borderRadius: 6, padding: "7px 9px", background: "rgba(255,255,255,0.018)", fontSize: "0.72rem" }}>
                  <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    {symbol} <ExternalLink size={12} />
                  </Link>
                  <span style={{ color: "var(--text-secondary)", fontSize: "0.74rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{reasons(item)}</span>
                  <span style={{ color: "#93c5fd", fontSize: "0.68rem", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <Search size={11} /> 🔍 Early Signal · {Number(item.confidence_score || 0).toFixed(1)}%
                  </span>
                </article>
              );
            })}
          </div>
        )
      )}
    </section>
  );
}