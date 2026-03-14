"use client";
/**
 * /journal — Trade Journal Page
 * Filterable, paginated trade history + Today's live signal log.
 */
import { useEffect, useState, useCallback } from "react";
import { api, JournalTrade, SignalToday } from "@/lib/api";
import { Search, TrendingUp, TrendingDown, ChevronLeft, ChevronRight, Zap } from "lucide-react";

const LIMIT = 50;

function fmt(v: number | null | undefined, decimals = 2) {
  if (v === null || v === undefined) return "—";
  return v.toFixed(decimals);
}

function timeLabel(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  } catch {
    return ts;
  }
}

// ─── Today's Signals Section ───────────────────────────────────────────────
function TodaySignals() {
  const [signals, setSignals] = useState<SignalToday[]>([]);
  const [date, setDate] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api.signalsToday()
      .then((r) => { setSignals(r.signals); setDate(r.date); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{
        padding: "14px 18px",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}>
        <Zap size={15} style={{ color: "#f0c060" }} />
        <span style={{ fontWeight: 600 }}>Today&apos;s Signals</span>
        <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {date} · auto-refresh 30s
        </span>
      </div>

      {loading ? (
        <div style={{ padding: "24px", textAlign: "center", color: "var(--text-secondary)" }}>Loading…</div>
      ) : signals.length === 0 ? (
        <div style={{ padding: "24px", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
          No signals generated today yet. Signals appear here as the engine fires them during market hours.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Dir</th>
                <th>Setup</th>
                <th>Entry</th>
                <th>Stop Loss</th>
                <th>Target 1</th>
                <th>Target 2</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.signal_id}>
                  <td style={{ fontFamily: "monospace", fontSize: "0.78rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {timeLabel(s.created_at)}
                  </td>
                  <td style={{ fontWeight: 600 }}>{s.symbol?.replace("NSE:", "") ?? "—"}</td>
                  <td>
                    <span className={`badge ${s.direction === "LONG" ? "badge-long" : "badge-short"}`}>
                      {s.direction === "LONG" ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {s.direction ?? "—"}
                    </span>
                  </td>
                  <td style={{ fontSize: "0.77rem", color: "var(--text-secondary)" }}>{s.strategy_name ?? "—"}</td>
                  <td style={{ fontFamily: "monospace" }}>{fmt(s.entry)}</td>
                  <td style={{ fontFamily: "monospace", color: "#ff4e6a" }}>{fmt(s.stop_loss)}</td>
                  <td style={{ fontFamily: "monospace", color: "#00d18c" }}>{fmt(s.target1)}</td>
                  <td style={{ fontFamily: "monospace", color: "#00d18c" }}>{fmt(s.target2)}</td>
                  <td style={{ fontFamily: "monospace" }}>{fmt(s.score)}</td>
                  <td style={{ fontFamily: "monospace" }}>
                    {s.confidence != null ? `${fmt(s.confidence, 1)}%` : "—"}
                  </td>
                  <td>
                    {s.result ? (
                      <span className={`badge ${s.result === "WIN" ? "badge-win" : s.result === "LOSS" ? "badge-loss" : "badge-neutral"}`}>
                        {s.result}
                      </span>
                    ) : (
                      <span className="badge badge-neutral">PENDING</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Main Journal Page ─────────────────────────────────────────────────────
export default function JournalPage() {
  const [trades,   setTrades  ] = useState<JournalTrade[]>([]);
  const [total,    setTotal   ] = useState(0);
  const [offset,   setOffset  ] = useState(0);
  const [loading,  setLoading ] = useState(true);

  const [symbol,    setSymbol   ] = useState("");
  const [setup,     setSetup    ] = useState("");
  const [result,    setResult   ] = useState("");
  const [direction, setDirection] = useState("");
  const [dateFrom,  setDateFrom ] = useState("");
  const [dateTo,    setDateTo   ] = useState("");
  const [symbols,   setSymbols  ] = useState<string[]>([]);
  const [setups,    setSetups   ] = useState<string[]>([]);

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
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const totalPages = Math.ceil(total / LIMIT);
  const page       = Math.floor(offset / LIMIT) + 1;

  const wins  = trades.filter(t => t.result === "WIN").length;
  const losses = trades.filter(t => t.result === "LOSS").length;
  const totalR = trades.reduce((s, t) => s + (t.pnl_r ?? 0), 0);

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, margin: 0 }}>Trade Journal</h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
          {total} trades · {wins} W / {losses} L · {totalR >= 0 ? "+" : ""}{totalR.toFixed(2)}R (this view)
        </p>
      </div>

      {/* Today's live signals — always shown at top */}
      <TodaySignals />

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

      {/* Historical Trades Table */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", fontWeight: 600, fontSize: "0.9rem" }}>
          Historical Trade Log
        </div>
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
                  <tr><td colSpan={8} style={{ textAlign: "center", padding: 40, color: "var(--text-secondary)" }}>
                    No historical trades found. Push trades via sync or check trade_ledger_2026.csv.
                  </td></tr>
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
