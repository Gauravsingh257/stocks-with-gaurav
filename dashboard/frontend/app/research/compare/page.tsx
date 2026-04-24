"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { api, type SwingIdea, type LongTermIdea } from "@/lib/api";

type IdeaUnion = SwingIdea | LongTermIdea;

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctColor(v: number | null | undefined): string {
  if (v == null) return "var(--text-dim)";
  return v >= 0 ? "#00e096" : "#ff4757";
}

interface MetricRow {
  label: string;
  values: (string | number | null | undefined)[];
  color?: (v: string | number | null | undefined) => string;
}

export default function ComparePage() {
  return (
    <Suspense fallback={<div style={{ padding: 40, color: "var(--text-secondary)" }}>Loading...</div>}>
      <CompareContent />
    </Suspense>
  );
}

function CompareContent() {
  const searchParams = useSearchParams();
  const symbolsParam = searchParams.get("symbols") || "";
  const symbols = symbolsParam.split(",").filter(Boolean).slice(0, 3);

  const [ideas, setIdeas] = useState<Map<string, IdeaUnion>>(new Map());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (symbols.length === 0) return;
    setLoading(true);
    Promise.all([
      api.swingResearch(50).catch(() => ({ items: [] })),
      api.longtermResearch(50).catch(() => ({ items: [] })),
    ]).then(([swingRes, ltRes]) => {
      const map = new Map<string, IdeaUnion>();
      [...(swingRes.items || []), ...(ltRes.items || [])].forEach((item) => {
        const sym = item.symbol.replace("NSE:", "");
        if (symbols.includes(sym) && !map.has(sym)) {
          map.set(sym, item);
        }
      });
      setIdeas(map);
      setLoading(false);
    });
  }, [symbolsParam]); // eslint-disable-line react-hooks/exhaustive-deps

  if (symbols.length < 2) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <p style={{ color: "var(--text-secondary)", marginBottom: 12 }}>Select at least 2 stocks from the Research page to compare.</p>
        <Link href="/research" style={{ color: "#5b9cf6" }}>Back to Research</Link>
      </div>
    );
  }

  const items = symbols.map((s) => ideas.get(s));

  const metrics: MetricRow[] = [
    { label: "Entry Price", values: items.map((i) => i ? `₹${fmt(i.entry_price)}` : "—") },
    { label: "Stop Loss", values: items.map((i) => i ? `₹${fmt(i.stop_loss)}` : "—") },
    {
      label: "Target",
      values: items.map((i) => {
        if (!i) return "—";
        const t = "target_1" in i ? (i as SwingIdea).target_1 : (i as LongTermIdea).long_term_target;
        return t ? `₹${fmt(t)}` : "—";
      }),
    },
    { label: "R:R", values: items.map((i) => i ? `${i.risk_reward.toFixed(1)}x` : "—") },
    {
      label: "Confidence",
      values: items.map((i) => i ? `${i.confidence_score.toFixed(1)}%` : "—"),
      color: (v) => {
        const n = typeof v === "string" ? parseFloat(v) : 0;
        return n >= 70 ? "#00e096" : n >= 50 ? "#f59e0b" : "#ff4757";
      },
    },
    { label: "Sector", values: items.map((i) => i?.sector || "—") },
    { label: "PE Ratio", values: items.map((i) => i?.pe_ratio != null ? String(i.pe_ratio) : "—") },
    { label: "Market Cap", values: items.map((i) => i?.market_cap_cr != null ? `${i.market_cap_cr.toLocaleString()} Cr` : "—") },
    { label: "ROE %", values: items.map((i) => i?.roe_pct != null ? `${i.roe_pct}%` : "—") },
    { label: "D/E", values: items.map((i) => i?.debt_equity != null ? String(i.debt_equity) : "—") },
    { label: "Setup", values: items.map((i) => i?.setup || "—") },
    { label: "Action", values: items.map((i) => i?.action_tag || "—") },
    { label: "CMP", values: items.map((i) => i?.scan_cmp ? `₹${fmt(i.scan_cmp)}` : "—") },
    {
      label: "Entry Gap",
      values: items.map((i) => i?.entry_gap_pct != null ? `${i.entry_gap_pct > 0 ? "+" : ""}${i.entry_gap_pct}%` : "—"),
      color: (v) => pctColor(typeof v === "string" ? parseFloat(v) : null),
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Link href="/research" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem" }}>
          <ArrowLeft size={16} /> Research
        </Link>
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Compare Stocks</h1>
      </div>

      {loading ? (
        <div className="glass" style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>Loading comparison data...</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ minWidth: 500 }}>
            <thead>
              <tr>
                <th style={{ width: 140 }}>Metric</th>
                {symbols.map((s) => (
                  <th key={s} style={{ textAlign: "center" }}>
                    <a href={`https://www.tradingview.com/chart/?symbol=NSE:${s}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", textDecoration: "none" }}>
                      {s}
                    </a>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map((row) => (
                <tr key={row.label}>
                  <td style={{ fontWeight: 600, fontSize: "0.78rem", color: "var(--text-secondary)" }}>{row.label}</td>
                  {row.values.map((v, i) => (
                    <td key={i} style={{
                      textAlign: "center", fontSize: "0.82rem", fontWeight: 500,
                      color: row.color ? row.color(v) : "var(--text-primary)",
                    }}>
                      {v ?? "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
