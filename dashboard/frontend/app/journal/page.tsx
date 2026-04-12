"use client";
/**
 * /journal — Trade Journal Page
 * Three tabs: Intraday Trades | Swing Ideas | Long-Term Ideas
 */
import { useEffect, useState, useCallback } from "react";
import { api, JournalTrade, SignalLogEntry, JournalIdeaRow } from "@/lib/api";
import { pnlColor, StatusBadge } from "@/components/StatusBadge";
import { Search, TrendingUp, TrendingDown, ChevronLeft, ChevronRight, Zap } from "lucide-react";

const LIMIT = 50;
const SIGNAL_LIMIT = 50;

const SIGNAL_KINDS = [
  "",
  "ENTRY",
  "EXIT_TARGET",
  "EXIT_STOP",
  "EMA_CROSS",
  "CATCHUP",
  "MANUAL_DETECT",
  "ZONE_TAP_1M",
] as const;

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

function localISODate(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// ─── Telegram signal log (signal_log DB) ─────────────────────────────────────
function TelegramSignalLog() {
  const [signals, setSignals] = useState<SignalLogEntry[]>([]);
  const [rangeLabel, setRangeLabel] = useState("");
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [dateFrom, setDateFrom] = useState(localISODate);
  const [dateTo, setDateTo] = useState(localISODate);
  const [symFilter, setSymFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    api.signals({
      date_from: dateFrom,
      date_to: dateTo,
      symbol: symFilter.trim() || undefined,
      signal_kind: kindFilter || undefined,
      limit: SIGNAL_LIMIT,
      offset,
    })
      .then((r) => {
        setSignals(r.signals);
        setTotal(r.total);
        setRangeLabel(`${r.date_from} → ${r.date_to}`);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, symFilter, kindFilter, offset]);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") load();
    }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / SIGNAL_LIMIT));
  const page = Math.floor(offset / SIGNAL_LIMIT) + 1;

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{
        padding: "14px 18px",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 8,
      }}>
        <Zap size={15} style={{ color: "#f0c060" }} />
        <span style={{ fontWeight: 600 }}>Telegram signal log</span>
        <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {rangeLabel} · {total} row(s) · auto-refresh 30s
        </span>
      </div>

      <div className="flex flex-wrap gap-2 items-center p-3 border-b" style={{ borderColor: "var(--border)" }}>
        <input type="date" className="input-dark" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setOffset(0); }} title="From" />
        <input type="date" className="input-dark" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setOffset(0); }} title="To" />
        <input className="input-dark" placeholder="Symbol…" value={symFilter}
          onChange={(e) => { setSymFilter(e.target.value.toUpperCase()); setOffset(0); }} style={{ width: 120 }} />
        <select className="input-dark" value={kindFilter} onChange={(e) => { setKindFilter(e.target.value); setOffset(0); }} style={{ width: 160 }}>
          <option value="">All kinds</option>
          {SIGNAL_KINDS.filter(Boolean).map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        <button className="btn-accent" type="button" onClick={() => { setDateFrom(localISODate()); setDateTo(localISODate()); setSymFilter(""); setKindFilter(""); setOffset(0); }}>
          Today
        </button>
      </div>

      {loading ? (
        <div style={{ padding: "24px", textAlign: "center", color: "var(--text-secondary)" }}>Loading…</div>
      ) : signals.length === 0 ? (
        <div style={{ padding: "24px", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
          No rows in signal_log for this range. Entries appear when the engine successfully sends Telegram alerts (and on exits). Ensure the engine runs against this machine&apos;s <code style={{ fontSize: "0.8rem" }}>ai_learning/data/trade_learning.db</code>.
        </div>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Kind</th>
                  <th>Symbol</th>
                  <th>Dir</th>
                  <th>Setup</th>
                  <th>Entry</th>
                  <th>Stop Loss</th>
                  <th>Target 1</th>
                  <th>Target 2</th>
                  <th>Score</th>
                  <th>Conf</th>
                  <th>Result</th>
                  <th>PnL R</th>
                  <th>Format</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.signal_id}>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                      {timeLabel(s.created_at)}
                    </td>
                    <td style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>{s.signal_kind || "—"}</td>
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
                        <span className="badge badge-neutral">—</span>
                      )}
                    </td>
                    <td style={{ fontFamily: "monospace" }}>{s.pnl_r != null ? `${s.pnl_r >= 0 ? "+" : ""}${fmt(s.pnl_r)}` : "—"}</td>
                    <td style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>{s.delivery_format ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{
            padding: "10px 14px", borderTop: "1px solid var(--border)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
              Page {page} / {totalPages}
            </span>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn-accent" type="button" disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - SIGNAL_LIMIT))}
                style={{ padding: "4px 10px", opacity: offset === 0 ? 0.4 : 1 }}
                aria-label="Previous page">
                <ChevronLeft size={14} />
              </button>
              <button className="btn-accent" type="button" disabled={offset + SIGNAL_LIMIT >= total}
                onClick={() => setOffset(offset + SIGNAL_LIMIT)}
                style={{ padding: "4px 10px", opacity: offset + SIGNAL_LIMIT >= total ? 0.4 : 1 }}
                aria-label="Next page">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─── Main Journal Page ─────────────────────────────────────────────────────
// ─── Research Ideas Tab (Swing / Long-Term) ──────────────────────────────────

const IDEA_STATUS = ["", "RUNNING", "TARGET_HIT", "STOP_HIT", "PENDING"] as const;

function fmtDate(s: string | null) {
  if (!s) return "—";
  return s.slice(0, 10);
}

function IdeasTab({ agentType, color }: { agentType: "SWING" | "LONGTERM"; color: string }) {
  const [ideas,   setIdeas  ] = useState<JournalIdeaRow[]>([]);
  const [total,   setTotal  ] = useState(0);
  const [offset,  setOffset ] = useState(0);
  const [loading, setLoading] = useState(true);
  const [symbol,  setSymbol ] = useState("");
  const [status,  setStatus ] = useState("");
  const [dateFrom,setDateFrom] = useState("");
  const [dateTo,  setDateTo ] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    const fn = agentType === "SWING" ? api.swingIdeas : api.longtermIdeas;
    fn({
      symbol: symbol.trim() || undefined,
      status: status || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      limit: 50,
      offset,
    })
      .then((r) => { setIdeas(r.ideas); setTotal(r.total); })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [agentType, symbol, status, dateFrom, dateTo, offset]);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") load();
    }, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / 50));
  const page = Math.floor(offset / 50) + 1;

  const active  = ideas.filter(r => r.status === "RUNNING").length;
  const hits    = ideas.filter(r => r.status === "TARGET_HIT").length;
  const stops   = ideas.filter(r => r.status === "STOP_HIT").length;
  const avgPnl  = ideas.length > 0 ? ideas.reduce((s, r) => s + r.profit_loss_pct, 0) / ideas.length : 0;

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Quick stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
        {[
          { l: "Total", v: total, c: "var(--text-primary)" },
          { l: "Active", v: active, c: "#5b9cf6" },
          { l: "Target Hit", v: hits, c: "#00d18c" },
          { l: "Stop Hit", v: stops, c: "#ff4d4d" },
          { l: "Avg P&L", v: `${avgPnl >= 0 ? "+" : ""}${avgPnl.toFixed(1)}%`, c: pnlColor(avgPnl) },
        ].map(({ l, v, c }) => (
          <div className="stat-card" key={l} style={{ padding: "12px 14px" }}>
            <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", letterSpacing: ".07em", marginBottom: 4 }}>{l.toUpperCase()}</div>
            <div style={{ fontSize: "1.2rem", fontWeight: 700, color: c }}>{v}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="glass flex flex-wrap gap-3 items-center p-3">
        <input className="input-dark" placeholder="Symbol…" value={symbol}
          onChange={e => { setSymbol(e.target.value.toUpperCase()); setOffset(0); }} style={{ width: 130 }} />
        <select className="input-dark" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}>
          {IDEA_STATUS.map(s => <option key={s} value={s}>{s || "All Status"}</option>)}
        </select>
        <input type="date" className="input-dark" value={dateFrom}
          onChange={e => { setDateFrom(e.target.value); setOffset(0); }} title="From" />
        <input type="date" className="input-dark" value={dateTo}
          onChange={e => { setDateTo(e.target.value); setOffset(0); }} title="To" />
        <button className="btn-accent" onClick={() => { setSymbol(""); setStatus(""); setDateFrom(""); setDateTo(""); setOffset(0); }}>
          Reset
        </button>
      </div>

      {/* Table */}
      <div className="glass" style={{ overflow: "hidden" }}>
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", fontWeight: 600, fontSize: "0.9rem", display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 3, height: 18, borderRadius: 3, background: color }} />
          {agentType === "SWING" ? "Swing Ideas" : "Long-Term Ideas"}
          <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-secondary)" }}>{total} records</span>
        </div>
        <div style={{ overflowX: "auto", maxHeight: 480 }}>
          {loading ? (
            <div style={{ padding: "40px", textAlign: "center", color: "var(--text-secondary)" }}>Loading…</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th><th>Symbol</th><th>Setup</th><th>Entry ₹</th>
                  <th>CMP ₹</th><th>P&L%</th><th>Days</th><th>Status</th><th>Recommended</th><th>Reasoning</th>
                </tr>
              </thead>
              <tbody>
                {ideas.length === 0 ? (
                  <tr><td colSpan={10} style={{ textAlign: "center", padding: 40, color: "var(--text-secondary)" }}>
                    No ideas yet — run a scan first from the Research page.
                  </td></tr>
                ) : ideas.map((r, i) => (
                  <tr key={r.id}>
                    <td style={{ color: "var(--text-dim)" }}>{offset + i + 1}</td>
                    <td style={{ fontWeight: 700, color: "var(--text-primary)" }}>{r.symbol}</td>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.78rem" }}>{r.setup ?? "—"}</td>
                    <td style={{ fontFamily: "monospace" }}>₹{r.entry_price?.toFixed(2)}</td>
                    <td style={{ fontFamily: "monospace" }}>
                      {r.current_price ? `₹${r.current_price.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ fontFamily: "monospace", fontWeight: 700, color: pnlColor(r.profit_loss_pct) }}>
                      {r.profit_loss_pct >= 0 ? "+" : ""}{r.profit_loss_pct.toFixed(1)}%
                    </td>
                    <td>{r.days_held}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>{fmtDate(r.recommended_at)}</td>
                    <td style={{ fontSize: "0.72rem", color: "var(--text-dim)", maxWidth: 260, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {r.reasoning_summary || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>Page {page} of {totalPages}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn-accent" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 50))}
              style={{ padding: "5px 10px", opacity: offset === 0 ? 0.4 : 1 }}
              aria-label="Previous page">
              <ChevronLeft size={14} />
            </button>
            <button className="btn-accent" disabled={offset + 50 >= total}
              onClick={() => setOffset(offset + 50)}
              style={{ padding: "5px 10px", opacity: offset + 50 >= total ? 0.4 : 1 }}
              aria-label="Next page">
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Tab bar ──────────────────────────────────────────────────────────────────

type Tab = "intraday" | "swing" | "longterm";

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const tabs: { id: Tab; label: string; color: string }[] = [
    { id: "intraday", label: "Intraday Trades", color: "#5b9cf6" },
    { id: "swing",    label: "Swing Ideas",     color: "#00d18c" },
    { id: "longterm", label: "Long-Term Ideas",  color: "#a78bfa" },
  ];
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--border)", paddingBottom: 0 }}>
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            padding: "9px 18px",
            fontSize: "0.84rem",
            fontWeight: active === t.id ? 700 : 500,
            color: active === t.id ? t.color : "var(--text-secondary)",
            background: "transparent",
            border: "none",
            borderBottom: active === t.id ? `2px solid ${t.color}` : "2px solid transparent",
            cursor: "pointer",
            transition: "all .15s",
            marginBottom: -1,
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function JournalPage() {
  const [activeTab, setActiveTab] = useState<Tab>("intraday");

  // Intraday state
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
    if (activeTab !== "intraday") return;
    load();
    const t = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "hidden") load();
    }, 60_000);
    return () => clearInterval(t);
  }, [load, activeTab]);

  const totalPages = Math.ceil(total / LIMIT);
  const page       = Math.floor(offset / LIMIT) + 1;
  const wins       = trades.filter(t => t.result === "WIN").length;
  const losses     = trades.filter(t => t.result === "LOSS").length;
  const totalR     = trades.reduce((s, t) => s + (t.pnl_r ?? 0), 0);

  return (
    <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <h1 className="text-xl md:text-2xl lg:text-3xl font-bold m-0">Trade Journal</h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", margin: "3px 0 0" }}>
          All trade records — intraday · swing picks · long-term ideas
        </p>
      </div>

      {/* Tab bar */}
      <TabBar active={activeTab} onChange={(t) => setActiveTab(t)} />

      {/* ── INTRADAY TAB ─────────────────────────────────────────────── */}
      {activeTab === "intraday" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.78rem", margin: 0 }}>
            {total} trades · {wins} W / {losses} L · {totalR >= 0 ? "+" : ""}{totalR.toFixed(2)}R (this view)
          </p>

          {/* Telegram signal log */}
          <TelegramSignalLog />

          {/* Filters */}
          <div className="glass flex flex-col md:flex-row flex-wrap gap-3 md:gap-2.5 items-center p-4 md:px-4 md:py-3.5">
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
            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                Page {page} of {totalPages} · {total} records
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn-accent" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                  style={{ padding: "5px 10px", opacity: offset === 0 ? 0.4 : 1 }}
                  aria-label="Previous page">
                  <ChevronLeft size={14} />
                </button>
                <button className="btn-accent" disabled={offset + LIMIT >= total}
                  onClick={() => setOffset(offset + LIMIT)}
                  style={{ padding: "5px 10px", opacity: offset + LIMIT >= total ? 0.4 : 1 }}
                  aria-label="Next page">
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── SWING TAB ──────────────────────────────────────────────────── */}
      {activeTab === "swing" && <IdeasTab agentType="SWING" color="#00d18c" />}

      {/* ── LONG-TERM TAB ──────────────────────────────────────────────── */}
      {activeTab === "longterm" && <IdeasTab agentType="LONGTERM" color="#a78bfa" />}
    </div>
  );
}
