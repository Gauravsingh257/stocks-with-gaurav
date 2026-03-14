"use client";
/**
 * /journal — Trade Journal Page
 * Filterable, paginated trade history.
 */
import { useEffect, useState, useCallback } from "react";
import { api, JournalTrade } from "@/lib/api";
import { Search, TrendingUp, TrendingDown, ChevronLeft, ChevronRight } from "lucide-react";

const LIMIT = 50;

export default function JournalPage() {
  const [trades,   setTrades  ] = useState<JournalTrade[]>([]);
  const [total,    setTotal   ] = useState(0);
  const [offset,   setOffset  ] = useState(0);
  const [loading,  setLoading ] = useState(true);

  // Filters
  const [symbol,    setSymbol   ] = useState("");
  const [setup,     setSetup    ] = useState("");
  const [result,    setResult   ] = useState("");
  const [direction, setDirection] = useState("");
  const [dateFrom,  setDateFrom ] = useState("");
  const [dateTo,    setDateTo   ] = useState("");
  const [symbols,   setSymbols  ] = useState<string[]>([]);
  const [setups,    setSetups   ] = useState<string[]>([]);

  // Load dropdowns once
  useEffect(() => {
    api.symbols().then((r) => setSymbols(r.symbols));
    api.setups().then((r)  => setSetups(r.setups));
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    api.journal({
      symbol: symbol || undefined, setup: setup || undefined,
      result: result || undefined, direction: direction || undefined,
      date_from: dateFrom || undefined, date_to: dateTo || undefined,
      limit: LIMIT, offset,
    })
      .then((r) => { setTrades(r.trades); setTotal(r.total); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [symbol, setup, result, direction, dateFrom, dateTo, offset]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);  // auto-refresh every 60s (matches CSV watcher)
    return () => clearInterval(t);
  }, [load]);

  const totalPages = Math.ceil(total / LIMIT);
  const page       = Math.floor(offset / LIMIT) + 1;

  const wins  = trades.filter(t => t.result === "WIN").length;
  const losses= trades.filter(t => t.result === "LOSS").length;
  const totalR= trades.reduce((s, t) => s + (t.pnl_r ?? 0), 0);

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>Trade Journal</h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
          {total} trades · {wins} W / {losses} L · {totalR >= 0 ? "+" : ""}{totalR.toFixed(2)}R (this view)
        </p>
      </div>

      {/* Filters */}
      <div className="glass" style={{ padding: "14px 16px", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ position: "relative" }}>
          <Search size={13} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "var(--text-secondary)" }} />
          <input className="input-dark" placeholder="Symbol…" value={symbol}
            onChange={e => { setSymbol(e.target.value.toUpperCase()); setOffset(0); }}
            style={{ paddingLeft: 28, width: 120 }} />
        </div>

        <select className="input-dark" value={setup} onChange={e => { setSetup(e.target.value); setOffset(0); }} style={{ width: 170 }}>
          <option value="">All Setups</option>
          {setups.map(s => <option key={s}>{s}</option>)}
        </select>

        <select className="input-dark" value={result} onChange={e => { setResult(e.target.value); setOffset(0); }}>
          <option value="">All Results</option>
          <option value="WIN">WIN</option>
          <option value="LOSS">LOSS</option>
        </select>

        <select className="input-dark" value={direction} onChange={e => { setDirection(e.target.value); setOffset(0); }}>
          <option value="">All Directions</option>
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
        </select>

        <input type="date" className="input-dark" value={dateFrom} onChange={e => { setDateFrom(e.target.value); setOffset(0); }} title="From" />
        <input type="date" className="input-dark" value={dateTo}   onChange={e => { setDateTo(e.target.value);   setOffset(0); }} title="To" />

        <button className="btn-accent" onClick={() => { setSymbol(""); setSetup(""); setResult(""); setDirection(""); setDateFrom(""); setDateTo(""); setOffset(0); }}>
          Reset
        </button>
      </div>

      {/* Table */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          {loading ? (
            <div style={{ padding: "40px", textAlign: "center", color: "var(--text-secondary)" }}>Loading…</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th><th>Symbol</th><th>Dir</th><th>Setup</th>
                  <th>Entry</th><th>Exit</th><th>Result</th><th>PnL R</th>
                </tr>
              </thead>
              <tbody>
                {trades.length === 0 && (
                  <tr><td colSpan={8} style={{ textAlign: "center", padding: 40, color: "var(--text-secondary)" }}>No trades found</td></tr>
                )}
                {trades.map((t) => (
                  <tr key={t.id}>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.78rem" }}>
                      {t.date ? new Date(t.date).toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) : "—"}
                    </td>
                    <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>{t.symbol.replace("NSE:", "")}</td>
                    <td>
                      <span className={`badge ${t.direction === "LONG" ? "badge-long" : "badge-short"}`}>
                        {t.direction === "LONG" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                        {t.direction}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.77rem", color: "var(--text-secondary)" }}>{t.setup}</td>
                    <td style={{ fontFamily: "monospace" }}>{t.entry ?? "—"}</td>
                    <td style={{ fontFamily: "monospace" }}>{t.exit_price ?? "—"}</td>
                    <td>
                      <span className={`badge ${t.result === "WIN" ? "badge-win" : t.result === "LOSS" ? "badge-loss" : "badge-neutral"}`}>
                        {t.result}
                      </span>
                    </td>
                    <td style={{ fontFamily: "monospace", fontWeight: 600, color: (t.pnl_r ?? 0) >= 0 ? "var(--success)" : "var(--danger)" }}>
                      {(t.pnl_r ?? 0) >= 0 ? "+" : ""}{(t.pnl_r ?? 0).toFixed(2)}R
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        <div style={{
          padding: "12px 16px", borderTop: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
            Page {page} of {totalPages} · {total} records
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn-accent" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              style={{ padding: "5px 10px", opacity: offset === 0 ? 0.4 : 1 }}>
              <ChevronLeft size={14} />
            </button>
            <button className="btn-accent" disabled={offset + LIMIT >= total}
              onClick={() => setOffset(offset + LIMIT)}
              style={{ padding: "5px 10px", opacity: offset + LIMIT >= total ? 0.4 : 1 }}>
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
