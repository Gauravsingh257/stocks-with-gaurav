"use client";

import { useEffect, useMemo, useState } from "react";
import { Search, RefreshCw } from "lucide-react";
import { api, type StockUniverseResponse, type StockUniverseRow } from "@/lib/api";

type SortKey =
  | "turnover_cr" | "market_cap_cr" | "pe" | "roe_pct"
  | "debt_to_equity" | "revenue_growth_pct" | "net_margin_pct"
  | "ret_1y_pct" | "pct_from_52w_high" | "symbol";

const COLUMNS: { key: SortKey; label: string; hint: string; numeric: boolean }[] = [
  { key: "symbol", label: "Symbol", hint: "NSE ticker", numeric: false },
  { key: "market_cap_cr", label: "MCap", hint: "Market capitalisation", numeric: true },
  { key: "turnover_cr", label: "Traded/day", hint: "20-day average traded value", numeric: true },
  { key: "pe", label: "P/E", hint: "Price to earnings — what you pay per ₹1 of profit", numeric: true },
  { key: "roe_pct", label: "ROE %", hint: "Return on equity — how well profit is generated from shareholder capital", numeric: true },
  { key: "debt_to_equity", label: "D/E", hint: "Debt to equity — leverage. Lower is safer", numeric: true },
  { key: "net_margin_pct", label: "Margin %", hint: "Net profit margin", numeric: true },
  { key: "revenue_growth_pct", label: "Rev Gr %", hint: "Revenue growth", numeric: true },
  { key: "ret_1y_pct", label: "1Y %", hint: "One-year price return", numeric: true },
  { key: "pct_from_52w_high", label: "Off 52w High", hint: "How far below the 52-week high", numeric: true },
];

const OPTION_STYLE: React.CSSProperties = { background: "#0f1620", color: "#e6edf3" };

function num(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

/** Money on the Indian scale. 1120303 -> "11.2L Cr", 62332 -> "62.3K Cr".
 *  A seven-digit run of raw crores is unreadable at a glance and it is the
 *  column people scan first. */
function money(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e5) return `${(v / 1e5).toFixed(2)}L Cr`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(2)}K Cr`;
  if (a >= 100) return `${v.toFixed(0)} Cr`;
  // Below Rs100Cr the decimal carries real meaning — this column is how you
  // judge whether a name is liquid enough to trade, and 12.5 vs 13 is the
  // difference between passing and failing a liquidity screen.
  if (a >= 1) return `${v.toFixed(1)} Cr`;
  return `${v.toFixed(2)} Cr`;
}

/** A percentage, with the sign carried on the number itself. */
function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}%`;
}

/** Colour only where the sign genuinely means better/worse. */
function tone(key: SortKey, v: number | null | undefined): string | undefined {
  if (v === null || v === undefined) return undefined;
  if (key === "ret_1y_pct" || key === "revenue_growth_pct") {
    return v > 0 ? "var(--success, #16a34a)" : v < 0 ? "var(--danger, #dc2626)" : undefined;
  }
  if (key === "debt_to_equity") return v > 2 ? "var(--danger, #dc2626)" : undefined;
  if (key === "roe_pct") return v >= 15 ? "var(--success, #16a34a)" : undefined;
  return undefined;
}

export function StockUniverse() {
  const [data, setData] = useState<StockUniverseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [sector, setSector] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("turnover_cr");
  const [asc, setAsc] = useState(false);
  const [shown, setShown] = useState(100);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.stockUniverse()
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const rows = useMemo(() => {
    const items = data?.items ?? [];
    const term = search.trim().toUpperCase();
    const filtered = items.filter((r) =>
      (!sector || r.sector === sector) &&
      (!term || r.symbol.includes(term) || (r.company_name ?? "").toUpperCase().includes(term)));
    const dir = asc ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (sortKey === "symbol") return a.symbol.localeCompare(b.symbol) * dir;
      const av = a[sortKey] as number | null, bv = b[sortKey] as number | null;
      if (av === null || av === undefined) return 1;   // nulls always last
      if (bv === null || bv === undefined) return -1;
      return (av - bv) * dir;
    });
  }, [data, search, sector, sortKey, asc]);

  useEffect(() => { setShown(100); }, [search, sector, sortKey, asc]);

  if (loading) {
    return (
      <div className="glass" style={{ padding: 14 }}>
        <div style={{ fontWeight: 600 }}>Stock Universe</div>
        <div style={{ fontSize: "0.8rem", color: "var(--text-dim)", marginTop: 6 }}>Loading…</div>
      </div>
    );
  }
  if (!data?.available) {
    return (
      <div className="glass" style={{ padding: 14 }}>
        <div style={{ fontWeight: 600 }}>Stock Universe</div>
        <div style={{ fontSize: "0.8rem", color: "var(--text-dim)", marginTop: 6 }}>
          Not built yet — the table is populated by the weekly Saturday refresh.
        </div>
      </div>
    );
  }

  const refreshed = data.refreshed_at ? String(data.refreshed_at).slice(0, 16).replace("T", " ") : null;

  return (
    <div className="glass" style={{ padding: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontWeight: 600 }}>Stock Universe</div>
          <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", marginTop: 3 }}>
            Every NSE stock we scan, with sector and the numbers that judge it — what you pay (P/E),
            how well it earns (ROE), and how much it owes (D/E).
          </div>
        </div>
        <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 5 }}>
          <RefreshCw size={12} />
          {refreshed ? `Updated ${refreshed} · refreshes Saturdays` : "Refreshes Saturdays"}
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0 10px" }}>
        <div style={{ position: "relative", flex: "1 1 220px", minWidth: 180 }}>
          <Search size={14} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "var(--text-dim)" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search symbol or company…"
            aria-label="Search symbol or company"
            style={{
              width: "100%", padding: "9px 10px 9px 30px", fontSize: "0.82rem",
              borderRadius: 8, border: "1px solid var(--border-interactive)",
              background: "var(--bg-field)", color: "var(--text-primary)",
            }}
          />
        </div>
        <select
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          aria-label="Filter by sector"
          style={{
            padding: "9px 10px", fontSize: "0.82rem", borderRadius: 8,
            border: "1px solid var(--border-interactive)", background: "var(--bg-field)",
            color: "var(--text-primary)",
          }}
        >
          {/* The native popup is painted by the OS, which defaults to a white
              sheet — light grey option text on white was unreadable. Options
              carry their own explicit dark colours rather than inheriting. */}
          <option value="" style={OPTION_STYLE}>All sectors ({data.equities})</option>
          {(data.sectors ?? []).map((s) => (
            <option key={s.sector} value={s.sector} style={OPTION_STYLE}>
              {s.sector} ({s.count})
            </option>
          ))}
        </select>
      </div>

      <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginBottom: 6 }}>
        {rows.length.toLocaleString()} of {data.equities.toLocaleString()} stocks
        {data.with_fundamentals ? ` · ${data.with_fundamentals.toLocaleString()} with fundamentals` : ""}
      </div>

      <div style={{ overflowX: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.78rem" }}>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  title={`${c.hint} — click to sort`}
                  data-sortable=""
                  scope="col"
                  aria-sort={sortKey === c.key ? (asc ? "ascending" : "descending") : "none"}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      if (sortKey === c.key) setAsc(!asc); else { setSortKey(c.key); setAsc(false); }
                    }
                  }}
                  onClick={() => { if (sortKey === c.key) setAsc(!asc); else { setSortKey(c.key); setAsc(false); } }}
                  style={{
                    textAlign: c.numeric && c.key !== "symbol" ? "right" : "left",
                    padding: "8px 10px", whiteSpace: "nowrap", cursor: "pointer",
                    borderBottom: "1px solid var(--border)",
                    color: sortKey === c.key ? "var(--accent)" : "var(--text-dim)",
                    fontWeight: 600, fontSize: "0.7rem", letterSpacing: "0.03em",
                    textTransform: "uppercase", userSelect: "none",
                  }}
                >
                  {c.label}{sortKey === c.key ? (asc ? " ↑" : " ↓") : ""}
                </th>
              ))}
              <th style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid var(--border)", color: "var(--text-dim)", fontWeight: 600, fontSize: "0.7rem", textTransform: "uppercase" }}>Sector</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, shown).map((r: StockUniverseRow) => (
              <tr key={r.symbol} className="row-clickable">
                <td style={{ padding: "7px 10px", borderBottom: "1px solid var(--border-soft, var(--border))", whiteSpace: "nowrap" }}>
                  <div style={{ fontWeight: 600 }}>{r.symbol}</div>
                  {r.company_name && (
                    <div style={{ color: "var(--text-dim)", fontSize: "0.68rem", maxWidth: 190, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.company_name}
                    </div>
                  )}
                </td>
                {COLUMNS.filter((c) => c.key !== "symbol").map((c) => (
                  <td key={c.key} style={{
                    padding: "7px 10px", textAlign: "right", whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                    borderBottom: "1px solid var(--border-soft, var(--border))",
                    color: tone(c.key, r[c.key] as number | null),
                  }}>
                    {c.key === "market_cap_cr" || c.key === "turnover_cr"
                      ? money(r[c.key] as number | null)
                      : c.key === "pe" || c.key === "debt_to_equity"
                        ? num(r[c.key] as number | null, c.key === "debt_to_equity" ? 2 : 1)
                        : pct(r[c.key] as number | null)}
                  </td>
                ))}
                <td style={{ padding: "7px 10px", whiteSpace: "nowrap", color: "var(--text-secondary)", borderBottom: "1px solid var(--border-soft, var(--border))" }}>
                  {r.sector}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > shown && (
        <button
          onClick={() => setShown(shown + 200)}
          className="btn-accent"
          style={{ marginTop: 10, fontSize: "0.78rem" }}
        >
          Show more ({(rows.length - shown).toLocaleString()} remaining)
        </button>
      )}
    </div>
  );
}
