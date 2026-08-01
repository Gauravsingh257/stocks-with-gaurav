"use client";

/**
 * Track Record — the canonical lifecycle ledger.
 *
 * This page reads ONLY /api/lifecycle/*. It never touches research
 * recommendations. Previously it rendered `stock_recommendations`, which
 * records IDEAS rather than trades, so names that were never taken into any
 * book (TIL, STALLION, PNGJL, SENCO…) appeared as successful "Target Hit"
 * trades while real portfolio trades — SCANSTL closed at +51.18% — were absent
 * entirely, and the page's win rate could never agree with the books'.
 *
 * Two rules the layout enforces:
 *   1. Summary cards come from a SEPARATE stats call over the whole filtered
 *      set, never from the rows on screen — a page or a status filter can never
 *      become the headline.
 *   2. An idea that never filled is shown as Never Executed and carries no
 *      P&L. "The level we published was reached" is not "we held this".
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ShieldAlert, X } from "lucide-react";
import {
  api, LifecycleTrade, LifecycleStats, LifecycleFacets, LifecycleTimeline,
} from "@/lib/api";

const PAGE_SIZE = 50;

const PORTFOLIOS = ["ALL", "SWING", "LONGTERM", "MOMENTUM", "RESEARCH", "MANUAL", "PAPER"];
const STATUSES = [
  "ALL", "AWAITING_ENTRY", "ENTRY_TRIGGERED", "ACTIVE", "PARTIAL_EXIT",
  "TARGET_HIT", "STOP_HIT", "EXPIRED", "CANCELLED", "NEVER_EXECUTED",
];
const EXECUTION = ["ALL", "EXECUTED", "NEVER_EXECUTED"];
const ENGINES = ["ALL", "SMC", "MOMENTUM", "MANUAL", "AI"];
const MONTHS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CONFIDENCE = [{ l: "Any", v: "" }, { l: "90+", v: "90" }, { l: "80+", v: "80" },
                    { l: "70+", v: "70" }, { l: "60+", v: "60" }];
const OUTCOMES = ["ALL", "WINNER", "LOSER"];

const label = (s: string) =>
  s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

/** Executed outcomes get outcome colours; unexecuted states stay neutral so a
 *  never-taken idea can never look like a win. */
function statusStyle(s: string): { bg: string; fg: string } {
  switch (s) {
    case "TARGET_HIT": return { bg: "rgba(0,224,150,0.14)", fg: "#00e096" };
    case "STOP_HIT": return { bg: "rgba(255,71,87,0.14)", fg: "#ff4757" };
    case "ACTIVE":
    case "ENTRY_TRIGGERED":
    case "PARTIAL_EXIT":
    case "TRAILING_SL": return { bg: "rgba(0,212,255,0.12)", fg: "#00d4ff" };
    case "AWAITING_ENTRY": return { bg: "rgba(240,192,96,0.12)", fg: "#f0c060" };
    case "MANUAL_CLOSED": return { bg: "rgba(160,160,180,0.14)", fg: "#b9b9c6" };
    default: return { bg: "rgba(120,120,140,0.12)", fg: "#8b8b9a" };
  }
}

function StatCard({ label: l, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="glass" style={{ padding: "10px 14px", minWidth: 132, flexShrink: 0 }}>
      <div style={{ fontSize: "1.25rem", fontWeight: 700, color: color ?? "var(--text-primary)" }}>{value}</div>
      <div style={{ fontSize: "0.66rem", color: "var(--text-secondary)", marginTop: 2 }}>{l}</div>
      {sub && <div style={{ fontSize: "0.6rem", color: "var(--text-secondary)", opacity: 0.75 }}>{sub}</div>}
    </div>
  );
}

function Chips({ options, value, onChange, fmt }: {
  options: string[]; value: string; onChange: (v: string) => void; fmt?: (s: string) => string;
}) {
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {options.map((o) => (
        <button key={o} onClick={() => onChange(o)} style={{
          fontSize: "0.68rem", padding: "4px 10px", borderRadius: 6, cursor: "pointer",
          background: value === o ? "rgba(0,212,255,0.15)" : "transparent",
          border: `1px solid ${value === o ? "rgba(0,212,255,0.4)" : "rgba(255,255,255,0.08)"}`,
          color: value === o ? "#00d4ff" : "var(--text-secondary)",
        }}>{fmt ? fmt(o) : label(o)}</button>
      ))}
    </div>
  );
}

export default function TrackRecordPage() {
  const [portfolio, setPortfolio] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [execution, setExecution] = useState("ALL");
  const [engine, setEngine] = useState("ALL");
  const [month, setMonth] = useState(0);
  const [year, setYear] = useState<number | "">("");
  const [minConfidence, setMinConfidence] = useState("");
  const [outcome, setOutcome] = useState("ALL");
  const [page, setPage] = useState(0);

  const [rows, setRows] = useState<LifecycleTrade[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<LifecycleStats | null>(null);
  const [facets, setFacets] = useState<LifecycleFacets | null>(null);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<LifecycleTimeline | null>(null);

  // Filters are serialised once and shared by both calls, so the cards and the
  // table can never describe different populations.
  const filters = useMemo(() => ({
    portfolio, status, execution, engine,
    month: month || undefined,
    year: year || undefined,
    min_confidence: minConfidence || undefined,
    outcome,
  }), [portfolio, status, execution, engine, month, year, minConfidence, outcome]);

  useEffect(() => { api.lifecycleFacets().then(setFacets).catch(() => {}); }, []);
  useEffect(() => { setPage(0); }, [filters]);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.lifecycleTrades({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
      api.lifecycleStats(filters),
    ]).then(([t, s]) => {
      setRows(t.items ?? []);
      setTotal(t.total ?? 0);
      setStats(s);
    }).catch(() => { setRows([]); setTotal(0); })
      .finally(() => setLoading(false));
  }, [filters, page]);

  useEffect(() => { load(); }, [load]);
  // Keep in step with live book changes without a manual refresh.
  useEffect(() => {
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/research" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem" }}>
          <ArrowLeft size={16} /> Research
        </Link>
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Track Record</h1>
        <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(0,212,255,0.12)", color: "#00d4ff" }}>
          Full lifecycle ledger
        </span>
      </div>

      <div className="glass" style={{ padding: "10px 14px", display: "flex", gap: 8, alignItems: "flex-start", borderLeft: "3px solid #f0c060" }}>
        <ShieldAlert size={15} color="#f0c060" style={{ flexShrink: 0, marginTop: 2 }} />
        <p style={{ margin: 0, fontSize: "0.72rem", lineHeight: 1.5, color: "var(--text-secondary)" }}>
          Every signal across Swing, Long-Term, Momentum and Research, tracked from idea to outcome.
          An idea that never reached its entry is shown as <strong>Never Executed</strong> and carries no P&amp;L —
          only positions actually held contribute to win rate and returns.
          Past performance does not guarantee future results. Educational purposes only — not investment advice.
        </p>
      </div>

      {stats && (
        <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 4 }}>
          <StatCard label="Signals Generated" value={String(stats.signals_generated)} />
          <StatCard label="Entries Triggered" value={String(stats.entries_triggered)} sub={`${stats.execution_rate_pct}% execution rate`} />
          <StatCard label="Win Rate" value={stats.closed_trades ? `${stats.win_rate_pct}%` : "—"} sub={`${stats.wins}/${stats.closed_trades} closed`} color={stats.win_rate_pct >= 50 ? "#00e096" : "#f0c060"} />
          <StatCard label="Target Hit Rate" value={stats.closed_trades ? `${stats.target_hit_rate_pct}%` : "—"} sub={`${stats.target_hits} target`} color="#00e096" />
          <StatCard label="SL Rate" value={stats.closed_trades ? `${stats.sl_rate_pct}%` : "—"} sub={`${stats.stop_hits} stopped`} color="#ff4757" />
          <StatCard label="Avg Return" value={stats.closed_trades ? `${stats.avg_return_pct > 0 ? "+" : ""}${stats.avg_return_pct}%` : "—"} sub="per closed trade" color={stats.avg_return_pct >= 0 ? "#00e096" : "#ff4757"} />
          <StatCard label="Avg RR" value={stats.avg_rr != null ? `${stats.avg_rr}R` : "—"} />
          <StatCard label="Avg Holding" value={stats.avg_holding_days ? `${stats.avg_holding_days}d` : "—"} />
          <StatCard label="Open Trades" value={String(stats.open_trades)} color="#00d4ff" />
          <StatCard label="Pending Entries" value={String(stats.pending_entries)} color="#f0c060" />
          <StatCard label="Expired" value={String(stats.expired_signals)} />
          <StatCard label="Never Executed" value={String(stats.never_executed)} />
        </div>
      )}

      <div className="glass" style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
        <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>PORTFOLIO</div>
          <Chips options={PORTFOLIOS} value={portfolio} onChange={setPortfolio} /></div>
        <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>STATUS</div>
          <Chips options={STATUSES} value={status} onChange={setStatus} /></div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
          <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>EXECUTION</div>
            <Chips options={EXECUTION} value={execution} onChange={setExecution} /></div>
          <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>ENGINE</div>
            <Chips options={ENGINES} value={engine} onChange={setEngine} /></div>
          <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>OUTCOME</div>
            <Chips options={OUTCOMES} value={outcome} onChange={setOutcome} /></div>
          <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>CONFIDENCE</div>
            <Chips options={CONFIDENCE.map((c) => c.v)} value={minConfidence} onChange={setMinConfidence}
                   fmt={(v) => CONFIDENCE.find((c) => c.v === v)?.l ?? v} /></div>
        </div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
          <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>MONTH</div>
            <Chips options={MONTHS.map((_, i) => String(i))} value={String(month)}
                   onChange={(v) => setMonth(Number(v))} fmt={(v) => MONTHS[Number(v)]} /></div>
          {facets && facets.years.length > 0 && (
            <div><div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", marginBottom: 4 }}>YEAR</div>
              <Chips options={["", ...facets.years.map(String)]} value={String(year)}
                     onChange={(v) => setYear(v ? Number(v) : "")} fmt={(v) => v || "All"} /></div>
          )}
        </div>
      </div>

      <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>
        {loading ? "Loading…" : `${total} record(s) · page ${page + 1} of ${Math.max(pages, 1)}`}
        {status !== "ALL" && !loading && (
          <span style={{ color: "#f0c060" }}> · cards above always cover the full filtered set, never just this status</span>
        )}
      </div>

      <div className="glass" style={{ padding: 0, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
          <thead>
            <tr style={{ background: "rgba(255,255,255,0.03)", textAlign: "left" }}>
              {["SYMBOL", "BOOK", "SETUP", "ENTRY", "SL", "TARGET", "EXIT", "P&L %", "DAYS", "CONF.", "STATUS", "DATE"].map((h) => (
                <th key={h} style={{ padding: "9px 12px", fontSize: "0.62rem", color: "var(--text-secondary)", fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const st = statusStyle(r.status);
              return (
                <tr key={r.uuid} onClick={() => api.lifecycleTimeline(r.uuid).then(setDetail).catch(() => {})}
                    style={{ borderTop: "1px solid rgba(255,255,255,0.05)", cursor: "pointer" }}>
                  <td style={{ padding: "9px 12px", color: "#5b9cf6", fontWeight: 600, whiteSpace: "nowrap" }}>{r.symbol.replace("NSE:", "")}</td>
                  <td style={{ padding: "9px 12px" }}>
                    <span style={{ fontSize: "0.6rem", padding: "2px 6px", borderRadius: 4, background: "rgba(255,255,255,0.06)" }}>
                      {r.portfolio ?? r.source}
                    </span>
                  </td>
                  <td style={{ padding: "9px 12px", color: "var(--text-secondary)", fontSize: "0.68rem", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.setup ?? "—"}</td>
                  <td style={{ padding: "9px 12px" }}>{r.entry_price != null ? `₹${r.entry_price}` : "—"}</td>
                  <td style={{ padding: "9px 12px", color: "#ff4757" }}>{r.stop_loss != null ? `₹${r.stop_loss}` : "—"}</td>
                  <td style={{ padding: "9px 12px", color: "#00e096" }}>{r.target_1 != null ? `₹${r.target_1}` : "—"}</td>
                  <td style={{ padding: "9px 12px" }}>{r.exit_price != null ? `₹${r.exit_price}` : (r.executed ? "Open" : "—")}</td>
                  <td style={{ padding: "9px 12px", fontWeight: 700, color: r.pnl_pct == null ? "var(--text-secondary)" : r.pnl_pct >= 0 ? "#00e096" : "#ff4757" }}>
                    {r.pnl_pct == null ? "—" : `${r.pnl_pct > 0 ? "+" : ""}${r.pnl_pct}%`}
                  </td>
                  <td style={{ padding: "9px 12px", color: "var(--text-secondary)" }}>{r.holding_days != null ? `${r.holding_days}d` : "—"}</td>
                  <td style={{ padding: "9px 12px", color: "var(--text-secondary)" }}>{r.confidence != null ? Math.round(r.confidence) : "—"}</td>
                  <td style={{ padding: "9px 12px" }}>
                    <span style={{ fontSize: "0.62rem", padding: "3px 8px", borderRadius: 999, background: st.bg, color: st.fg, whiteSpace: "nowrap" }}>
                      {label(r.status)}
                    </span>
                  </td>
                  <td style={{ padding: "9px 12px", color: "var(--text-secondary)", fontSize: "0.68rem", whiteSpace: "nowrap" }}>
                    {(r.exit_at ?? r.created_at ?? "").slice(0, 10)}
                  </td>
                </tr>
              );
            })}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={12} style={{ padding: 26, textAlign: "center", color: "var(--text-secondary)" }}>No records match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "center" }}>
          <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}
                  style={{ padding: "5px 12px", fontSize: "0.72rem", borderRadius: 6, cursor: page === 0 ? "default" : "pointer", opacity: page === 0 ? 0.4 : 1, background: "transparent", border: "1px solid rgba(255,255,255,0.12)", color: "var(--text-primary)" }}>Previous</button>
          <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>{page + 1} / {pages}</span>
          <button disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}
                  style={{ padding: "5px 12px", fontSize: "0.72rem", borderRadius: 6, cursor: page + 1 >= pages ? "default" : "pointer", opacity: page + 1 >= pages ? 0.4 : 1, background: "transparent", border: "1px solid rgba(255,255,255,0.12)", color: "var(--text-primary)" }}>Next</button>
        </div>
      )}

      {detail?.found && detail.trade && (
        <div onClick={() => setDetail(null)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)", zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div className="glass" onClick={(e) => e.stopPropagation()} style={{ padding: 20, maxWidth: 620, width: "100%", maxHeight: "84vh", overflowY: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h2 style={{ margin: 0, fontSize: "1rem" }}>
                {detail.trade.symbol.replace("NSE:", "")}
                <span style={{ marginLeft: 8, fontSize: "0.66rem", padding: "2px 8px", borderRadius: 999, ...statusStyle(detail.trade.status) as object, background: statusStyle(detail.trade.status).bg, color: statusStyle(detail.trade.status).fg }}>
                  {label(detail.trade.status)}
                </span>
              </h2>
              <button onClick={() => setDetail(null)} style={{ background: "transparent", border: "none", color: "var(--text-secondary)", cursor: "pointer" }}><X size={18} /></button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 10, fontSize: "0.72rem", marginBottom: 14 }}>
              {[["Book", detail.trade.portfolio ?? detail.trade.source],
                ["Engine", detail.trade.engine ?? "—"],
                ["Entry", detail.trade.entry_price != null ? `₹${detail.trade.entry_price}` : "—"],
                ["Stop", detail.trade.stop_loss != null ? `₹${detail.trade.stop_loss}` : "—"],
                ["Target", detail.trade.target_1 != null ? `₹${detail.trade.target_1}` : "—"],
                ["Exit", detail.trade.exit_price != null ? `₹${detail.trade.exit_price}` : "—"],
                ["P&L", detail.trade.pnl_pct != null ? `${detail.trade.pnl_pct}%` : "—"],
                ["RR", detail.trade.rr_realized != null ? `${detail.trade.rr_realized}R` : "—"],
                ["Held", detail.trade.holding_days != null ? `${detail.trade.holding_days}d` : "—"]].map(([k, v]) => (
                <div key={String(k)}>
                  <div style={{ color: "var(--text-secondary)", fontSize: "0.62rem" }}>{k}</div>
                  <div style={{ fontWeight: 600 }}>{v}</div>
                </div>
              ))}
            </div>
            {detail.trade.setup && (
              <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", marginBottom: 12 }}>
                <strong>Setup:</strong> {detail.trade.setup}
              </div>
            )}
            <h3 style={{ fontSize: "0.78rem", margin: "0 0 8px" }}>Lifecycle</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {(detail.events ?? []).map((e, i) => (
                <div key={i} style={{ display: "flex", gap: 10, fontSize: "0.7rem", borderLeft: "2px solid rgba(0,212,255,0.35)", paddingLeft: 10 }}>
                  <span style={{ color: "var(--text-secondary)", minWidth: 132 }}>{(e.occurred_at ?? "").slice(0, 19).replace("T", " ")}</span>
                  <span>
                    <strong>{label(e.event)}</strong>
                    {e.from_status && e.to_status && e.from_status !== e.to_status &&
                      ` · ${label(e.from_status)} → ${label(e.to_status)}`}
                    {!e.from_status && e.to_status && ` · ${label(e.to_status)}`}
                    {e.price != null && ` · ₹${e.price}`}
                  </span>
                </div>
              ))}
              {(detail.events ?? []).length === 0 && (
                <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>No events recorded.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
