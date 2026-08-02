"use client";

/**
 * Lifecycle Analytics — the visual read on the ledger.
 *
 * Charts are plain SVG/CSS on purpose: the data volumes here are small, and a
 * charting dependency would add weight without adding accuracy.
 *
 * Every panel states its own basis. Monthly return sums per-trade percentages
 * within a month, which is a comparison across months — NOT a portfolio return.
 * Labelling it plainly is the point; the swing header once published exactly
 * that kind of sum as a return and overstated the book ~20-fold.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, TrendingUp, Layers, Filter, LogOut, GitBranch } from "lucide-react";
import {
  api, MonthlyPoint, EngineRow, FunnelResponse, ExitRow, ChainAttribution,
} from "@/lib/api";

const BOOKS = ["ALL", "SWING", "LONGTERM", "MOMENTUM"];
const label = (s: string) =>
  (s || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

function Panel({ icon, title, note, children }: {
  icon: React.ReactNode; title: string; note?: string; children: React.ReactNode;
}) {
  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        {icon}<h2 style={{ margin: 0, fontSize: "0.9rem" }}>{title}</h2>
      </div>
      {note && <p style={{ fontSize: "0.66rem", color: "var(--text-secondary)", margin: "0 0 12px" }}>{note}</p>}
      {children}
    </div>
  );
}

function MonthlyChart({ pts }: { pts: MonthlyPoint[] }) {
  if (!pts.length) return <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>No closed trades yet.</div>;
  const max = Math.max(...pts.map((p) => Math.abs(p.book_return_pct ?? 0)), 0.1);
  const cMin = Math.min(0, ...pts.map((p) => p.cumulative_book_return_pct ?? 0));
  const cMax = Math.max(0, ...pts.map((p) => p.cumulative_book_return_pct ?? 0));
  const cSpan = cMax - cMin || 1;
  const H = 150;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: H, marginBottom: 6, position: "relative" }}>
        <svg style={{ position: "absolute", inset: 0, width: "100%", height: H, pointerEvents: "none" }} preserveAspectRatio="none">
          <polyline
            fill="none" stroke="#00d4ff" strokeWidth="2"
            points={pts.map((p, i) =>
              `${(i + 0.5) / pts.length * 100}%,${H - (((p.cumulative_book_return_pct ?? 0) - cMin) / cSpan) * H}`).join(" ")}
          />
        </svg>
        {pts.map((p) => {
          const h = (Math.abs(p.book_return_pct ?? 0) / max) * (H * 0.72);
          const up = (p.book_return_pct ?? 0) >= 0;
          return (
            <div key={p.period} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}
                 title={`${p.period}: ${p.trades} trades · ${p.win_rate_pct}% win · book ${p.book_return_pct}% (sum of trades ${p.sum_pnl_pct}% over ${p.book_slots} slots) · cumulative ${p.cumulative_book_return_pct}%`}>
              <div style={{ height: Math.max(h, 2), background: up ? "rgba(0,224,150,0.5)" : "rgba(255,71,87,0.5)", borderRadius: "3px 3px 0 0" }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {pts.map((p) => (
          <div key={p.period} style={{ flex: 1, fontSize: "0.52rem", color: "var(--text-secondary)", textAlign: "center", whiteSpace: "nowrap", overflow: "hidden" }}>
            {p.period.slice(2)}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, fontSize: "0.66rem", color: "var(--text-secondary)" }}>
        Bars = book return that month · Line = cumulative book return. Each position is 1/{pts[0]?.book_slots ?? 20} of capital, so this is the portfolio move, not the sum of the trades.
      </div>
    </div>
  );
}

function FunnelView({ f }: { f: FunnelResponse }) {
  const top = f.stages[0]?.count || 1;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {f.stages.map((s, i) => {
        const w = Math.max((s.count / top) * 100, 2);
        return (
          <div key={s.stage}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", marginBottom: 3 }}>
              <span>{s.stage}</span>
              <span><strong>{s.count}</strong>
                {i > 0 && <span style={{ color: "var(--text-secondary)" }}> · {s.of_previous_pct}% of previous</span>}
              </span>
            </div>
            <div style={{ height: 18, background: "rgba(255,255,255,0.05)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${w}%`, height: "100%", background: `rgba(0,212,255,${0.55 - i * 0.1})` }} />
            </div>
          </div>
        );
      })}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 8, marginTop: 8, fontSize: "0.7rem" }}>
        {[["Still open", f.still_open], ["Awaiting entry", f.awaiting_entry],
          ["Expired", f.expired], ["Never executed", f.never_executed],
          ["Stopped out", f.stopped_out],
          ["Ideas never traded", f.leakage.ideas_that_never_traded]].map(([k, v]) => (
          <div key={String(k)}>
            <div style={{ color: "var(--text-secondary)", fontSize: "0.6rem" }}>{k}</div>
            <div style={{ fontWeight: 600 }}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Table({ cols, rows }: { cols: [string, (r: any) => React.ReactNode][]; rows: any[] }) {
  if (!rows.length) return <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>No data yet.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.74rem" }}>
        <thead><tr style={{ textAlign: "left" }}>
          {cols.map(([h]) => (
            <th key={h} style={{ padding: "6px 10px", fontSize: "0.6rem", color: "var(--text-secondary)", fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: "1px solid rgba(255,255,255,0.05)" }}>
              {cols.map(([h, fn]) => (
                <td key={h} style={{ padding: "7px 10px", whiteSpace: "nowrap" }}>{fn(r)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function LifecycleAnalyticsPage() {
  const [book, setBook] = useState("ALL");
  const [byVersion, setByVersion] = useState(false);
  const [monthly, setMonthly] = useState<MonthlyPoint[]>([]);
  const [engines, setEngines] = useState<EngineRow[]>([]);
  const [funnel, setFunnel] = useState<FunnelResponse | null>(null);
  const [exits, setExits] = useState<ExitRow[]>([]);
  const [chain, setChain] = useState<ChainAttribution | null>(null);

  useEffect(() => {
    api.lifecycleMonthly(book).then((r) => setMonthly(r.points ?? [])).catch(() => setMonthly([]));
    api.lifecycleFunnel(book).then(setFunnel).catch(() => setFunnel(null));
    api.lifecycleExits(book).then((r) => setExits(r.rows ?? [])).catch(() => setExits([]));
  }, [book]);
  useEffect(() => {
    api.lifecycleEngines(byVersion).then((r) => setEngines(r.rows ?? [])).catch(() => setEngines([]));
  }, [byVersion]);
  useEffect(() => {
    api.lifecycleChainAttribution().then(setChain).catch(() => setChain(null));
  }, []);

  const pnl = (v: number | null | undefined) =>
    v == null ? "—" : <span style={{ color: v >= 0 ? "#00e096" : "#ff4757" }}>{v > 0 ? "+" : ""}{v}%</span>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/research/track-record" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem" }}>
          <ArrowLeft size={16} /> Track Record
        </Link>
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Lifecycle Analytics</h1>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {BOOKS.map((b) => (
          <button key={b} onClick={() => setBook(b)} style={{
            fontSize: "0.7rem", padding: "5px 12px", borderRadius: 6, cursor: "pointer",
            background: book === b ? "rgba(0,212,255,0.15)" : "transparent",
            border: `1px solid ${book === b ? "rgba(0,212,255,0.4)" : "rgba(255,255,255,0.08)"}`,
            color: book === b ? "#00d4ff" : "var(--text-secondary)",
          }}>{label(b)}</button>
        ))}
      </div>

      <Panel icon={<TrendingUp size={15} color="#00e096" />} title="Monthly performance"
             note="Book-weighted: what the portfolio actually made each month.">
        <MonthlyChart pts={monthly} />
      </Panel>

      {funnel && (
        <Panel icon={<Filter size={15} color="#00d4ff" />} title="Conversion funnel"
               note="How published ideas become positions, and where they are lost.">
          <FunnelView f={funnel} />
        </Panel>
      )}

      <Panel icon={<Layers size={15} color="#f0c060" />}
             title={byVersion ? "Engine version comparison" : "Book comparison"}
             note="Same closed population for every row. Book return weights each position at 1/slots of capital; the raw sum is shown beside it for reference.">
        <button onClick={() => setByVersion((v) => !v)} style={{
          fontSize: "0.68rem", padding: "4px 10px", borderRadius: 6, marginBottom: 10, cursor: "pointer",
          background: "transparent", border: "1px solid rgba(255,255,255,0.12)", color: "var(--text-secondary)",
        }}>{byVersion ? "Show by book" : "Show by engine version"}</button>
        <Table
          rows={engines}
          cols={[
            [byVersion ? "VERSION" : "BOOK", (r) => <strong>{r.key}</strong>],
            ["CLOSED", (r) => r.closed_trades],
            ["WIN RATE", (r) => <span style={{ color: r.win_rate_pct >= 50 ? "#00e096" : "#f0c060" }}>{r.win_rate_pct}%</span>],
            ["TARGET RATE", (r) => `${r.target_hit_rate_pct}%`],
            ["BOOK RETURN", (r) => pnl(r.book_return_pct)],
            ["AVG P&L", (r) => pnl(r.avg_pnl_pct)],
            ["SUM OF TRADES", (r) => <span style={{ color: "var(--text-secondary)" }}>{r.sum_pnl_pct}%</span>],
            ["AVG RR", (r) => (r.avg_rr != null ? `${r.avg_rr}R` : "—")],
            ["AVG DAYS", (r) => (r.avg_holding_days != null ? `${r.avg_holding_days}d` : "—")],
          ]}
        />
      </Panel>

      <Panel icon={<LogOut size={15} color="#ff4757" />} title="Exit attribution"
             note="Which exit rule produced which outcome. Book impact is the portfolio move; sum of trades is the raw addition and is not a return.">
        <Table
          rows={exits}
          cols={[
            ["EXIT", (r) => <strong>{label(r.status)}</strong>],
            ["N", (r) => r.n],
            ["WIN RATE", (r) => `${r.win_rate_pct}%`],
            ["BOOK IMPACT", (r) => pnl(r.book_impact_pct)],
            ["AVG P&L", (r) => pnl(r.avg_pnl)],
            ["SUM OF TRADES", (r) => <span style={{ color: "var(--text-secondary)" }}>{r.sum_pnl}%</span>],
            ["AVG BEST", (r) => (r.avg_mfe != null ? `+${r.avg_mfe}%` : "—")],
            ["GIVEBACK", (r) => (r.avg_giveback_pct != null
              ? <span style={{ color: r.avg_giveback_pct >= 5 ? "#ff4757" : "var(--text-secondary)" }}>{r.avg_giveback_pct}%</span>
              : "—")],
            ["AVG DAYS", (r) => (r.avg_days != null ? `${r.avg_days}d` : "—")],
          ]}
        />
      </Panel>

      {chain && (
        <Panel icon={<GitBranch size={15} color="#c084fc" />} title="Cross-engine idea attribution"
               note={chain.basis}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 10, marginBottom: 12, fontSize: "0.74rem" }}>
            {[["Ideas tracked", chain.ideas_with_a_chain],
              ["Converted to a position", chain.ideas_converted_to_a_position],
              ["Conversion rate", `${chain.conversion_pct}%`],
              ["Traded by >1 engine", chain.ideas_traded_by_more_than_one_engine]].map(([k, v]) => (
              <div key={String(k)}>
                <div style={{ color: "var(--text-secondary)", fontSize: "0.62rem" }}>{k}</div>
                <div style={{ fontWeight: 700, fontSize: "1rem" }}>{v}</div>
              </div>
            ))}
          </div>
          <Table
            rows={chain.per_engine}
            cols={[
              ["ENGINE", (r) => <strong>{r.engine}</strong>],
              ["CONVERTED", (r) => r.converted],
              ["CLOSED", (r) => r.closed],
              ["WIN RATE", (r) => `${r.win_rate_pct}%`],
              ["SUM P&L", (r) => pnl(r.sum_pnl)],
            ]}
          />
        </Panel>
      )}
    </div>
  );
}
