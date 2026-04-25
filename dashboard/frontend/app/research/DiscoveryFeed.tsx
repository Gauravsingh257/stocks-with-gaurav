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
    <section className="glass" style={{ padding: 16, display: "grid", gap: 12 }}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, width: "100%", background: "transparent", border: 0, padding: 0, color: "inherit", cursor: "pointer", textAlign: "left" }}
      >
        <div>
          <h2 className="m-0 text-lg font-bold" style={{ color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>🔍 Early Signals (Experimental)</h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>Low-confidence candidates for exploration only</p>
        </div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.68rem", padding: "4px 8px", borderRadius: 6, background: "rgba(91,156,246,0.09)", border: "1px solid rgba(91,156,246,0.22)", color: "#5b9cf6", fontWeight: 800 }}>
          Exploration only · {items.length} <ChevronDown size={14} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.2s" }} />
        </span>
      </button>

      {open && (
        items.length === 0 ? (
          <div style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-secondary)", fontSize: "0.82rem", background: "rgba(255,255,255,0.02)" }}>
            No early experimental signals in the latest scan.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {items.map((item) => {
              const symbol = cleanSymbol(item.symbol);
              return (
                <article key={item.symbol} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 0.9fr) minmax(160px, 1fr) auto", gap: 10, alignItems: "center", border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "rgba(255,255,255,0.02)" }}>
                  <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    {symbol} <ExternalLink size={12} />
                  </Link>
                  <span style={{ color: "var(--text-secondary)", fontSize: "0.74rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{reasons(item)}</span>
                  <span style={{ color: "#5b9cf6", fontSize: "0.72rem", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 5 }}>
                    <Search size={12} /> Exploration only · {Number(item.confidence_score || 0).toFixed(1)}%
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