"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, BarChart, Bar, Cell, Legend,
} from "recharts";
import {
  BookMetrics, EquityPoint, BOOK_LABEL, BOOK_COLOR, fetchCombined,
  fmtINR, fmtPct, fmtNum, toneClass,
} from "@/lib/pil";

const ORDER = ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"];

type Row = { label: string; key: keyof BookMetrics; kind: "inr" | "pct" | "num" | "int"; tone?: boolean };

const SECTIONS: { title: string; rows: Row[] }[] = [
  {
    title: "Capital",
    rows: [
      { label: "Portfolio Value", key: "portfolio_value", kind: "inr" },
      { label: "Invested Capital", key: "invested_capital", kind: "inr" },
      { label: "Available Cash", key: "available_cash", kind: "inr" },
      { label: "Open Positions", key: "open_positions", kind: "int" },
      { label: "Pending Positions", key: "pending_positions", kind: "int" },
    ],
  },
  {
    title: "Returns",
    rows: [
      { label: "Total Return", key: "total_return_pct", kind: "pct", tone: true },
      { label: "Today", key: "today_return_pct", kind: "pct", tone: true },
      { label: "MTD", key: "mtd_pct", kind: "pct", tone: true },
      { label: "QTD", key: "qtd_pct", kind: "pct", tone: true },
      { label: "YTD", key: "ytd_pct", kind: "pct", tone: true },
      { label: "CAGR", key: "cagr_pct", kind: "pct", tone: true },
    ],
  },
  {
    title: "Risk",
    rows: [
      { label: "Max Drawdown", key: "max_drawdown_pct", kind: "pct", tone: true },
      { label: "Volatility", key: "volatility_pct", kind: "pct" },
      { label: "Sharpe", key: "sharpe", kind: "num", tone: true },
      { label: "Sortino", key: "sortino", kind: "num", tone: true },
      { label: "Calmar", key: "calmar", kind: "num", tone: true },
      { label: "Risk Score", key: "risk_score", kind: "num" },
    ],
  },
  {
    title: "Trade Quality",
    rows: [
      { label: "Hit Rate", key: "hit_rate_pct", kind: "pct" },
      { label: "Expectancy (₹)", key: "expectancy", kind: "inr", tone: true },
      { label: "Profit Factor", key: "profit_factor", kind: "num", tone: true },
      { label: "Avg Winner", key: "avg_winner", kind: "inr", tone: true },
      { label: "Avg Loser", key: "avg_loser", kind: "inr", tone: true },
      { label: "Win/Loss Ratio", key: "win_loss_ratio", kind: "num" },
      { label: "Avg Hold (days)", key: "avg_hold_days", kind: "num" },
      { label: "Turnover", key: "turnover_pct", kind: "pct" },
    ],
  },
];

function cellValue(m: BookMetrics | undefined, row: Row) {
  if (!m) return "—";
  const v = m[row.key] as number;
  if (row.kind === "inr") return fmtINR(v);
  if (row.kind === "pct") return fmtPct(v);
  if (row.kind === "int") return v ?? 0;
  return fmtNum(v);
}

export default function IntelligenceOverview() {
  const [metrics, setMetrics] = useState<Record<string, BookMetrics>>({});
  const [curves, setCurves] = useState<Record<string, EquityPoint[]>>({});
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const data = await fetchCombined();
        if (!live) return;
        const met: Record<string, BookMetrics> = {};
        const cur: Record<string, EquityPoint[]> = {};
        for (const b of Object.keys(data.books)) {
          met[b] = data.books[b].metrics;
          cur[b] = data.books[b].equity_curve;
        }
        setMetrics(met);
        setCurves(cur);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => { live = false; };
  }, []);

  // Combined equity curve, normalised so book curves are comparable (base 100).
  const equityData = useMemo(() => {
    const combined = curves["COMBINED"] || [];
    return combined.map((p) => ({ date: p.date, value: p.value }));
  }, [curves]);

  const normData = useMemo(() => {
    const dates = (curves["COMBINED"] || []).map((p) => p.date);
    return dates.map((d, i) => {
      const row: Record<string, number | string> = { date: d };
      for (const b of ["SWING", "LONGTERM", "MOMENTUM"]) {
        const c = curves[b] || [];
        const base = c[0]?.value || 1;
        const pt = c[Math.min(i, c.length - 1)];
        row[b] = pt ? Number(((pt.value / base) * 100).toFixed(2)) : 100;
      }
      return row;
    });
  }, [curves]);

  // Drawdown series from the combined curve (Part 9).
  const drawdownData = useMemo(() => {
    const c = curves["COMBINED"] || [];
    let peak = -Infinity;
    return c.map((p) => {
      peak = Math.max(peak, p.value);
      return { date: p.date, dd: peak ? Number((((p.value - peak) / peak) * 100).toFixed(2)) : 0 };
    });
  }, [curves]);

  // 30-day rolling annualised Sharpe from the combined curve (Part 9).
  const rollingSharpe = useMemo(() => {
    const c = curves["COMBINED"] || [];
    const rets: { date: string; r: number }[] = [];
    for (let i = 1; i < c.length; i++) {
      const prev = c[i - 1].value;
      if (prev) rets.push({ date: c[i].date, r: c[i].value / prev - 1 });
    }
    const W = 30;
    const out: { date: string; sharpe: number }[] = [];
    for (let i = W; i <= rets.length; i++) {
      const win = rets.slice(i - W, i).map((x) => x.r);
      const mean = win.reduce((s, v) => s + v, 0) / W;
      const sd = Math.sqrt(win.reduce((s, v) => s + (v - mean) ** 2, 0) / W);
      const sharpe = sd > 0 ? (mean * 252) / (sd * Math.sqrt(252)) : 0;
      out.push({ date: rets[i - 1].date, sharpe: Number(sharpe.toFixed(2)) });
    }
    return out;
  }, [curves]);

  const returnsBar = useMemo(
    () =>
      ["SWING", "LONGTERM", "MOMENTUM", "COMBINED"].map((b) => ({
        book: BOOK_LABEL[b],
        key: b,
        total: metrics[b]?.total_return_pct ?? 0,
        ytd: metrics[b]?.ytd_pct ?? 0,
        mtd: metrics[b]?.mtd_pct ?? 0,
      })),
    [metrics]
  );

  if (loading) return <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading portfolio intelligence…</div>;
  if (err)
    return (
      <div className="glass rounded-xl p-6 border border-rose-500/30 text-sm">
        <div className="text-rose-400 font-semibold mb-1">Could not load Portfolio Intelligence</div>
        <div className="text-[var(--text-secondary)]">{err}</div>
        <div className="text-[var(--text-dim)] text-xs mt-2">Ensure the backend has <code>PIL_ENABLED=1</code>.</div>
      </div>
    );

  const combined = metrics["COMBINED"];

  return (
    <div className="flex flex-col gap-6">
      {/* KPI header */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="Combined Value" value={fmtINR(combined?.portfolio_value)} />
        <Kpi label="Total Return" value={fmtPct(combined?.total_return_pct)} tone={combined?.total_return_pct} />
        <Kpi label="Today" value={fmtPct(combined?.today_return_pct)} tone={combined?.today_return_pct} />
        <Kpi label="Open / Pending" value={`${combined?.open_positions ?? 0} / ${combined?.pending_positions ?? 0}`} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="Combined Equity Curve">
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={equityData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={BOOK_COLOR.COMBINED} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={BOOK_COLOR.COMBINED} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-dim)" }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={54}
                tickFormatter={(v) => fmtINR(v)} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [fmtINR(Number(v)), "Value"]}
              />
              <Area type="monotone" dataKey="value" stroke={BOOK_COLOR.COMBINED} fill="url(#eq)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Per-Engine Growth (indexed to 100)">
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={normData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-dim)" }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={40} domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {["SWING", "LONGTERM", "MOMENTUM"].map((b) => (
                <Line key={b} type="monotone" dataKey={b} name={BOOK_LABEL[b]} stroke={BOOK_COLOR[b]} dot={false} strokeWidth={1.8} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      {/* Returns bar */}
      <Panel title="Return Comparison">
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={returnsBar} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
            <XAxis dataKey="book" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={40} tickFormatter={(v) => `${v}%`} />
            <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }}
              formatter={(v) => [`${Number(v)}%`, ""]} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="total" name="Total" radius={[3, 3, 0, 0]}>
              {returnsBar.map((r) => <Cell key={r.key} fill={BOOK_COLOR[r.key]} />)}
            </Bar>
            <Bar dataKey="ytd" name="YTD" radius={[3, 3, 0, 0]} fillOpacity={0.55}>
              {returnsBar.map((r) => <Cell key={r.key} fill={BOOK_COLOR[r.key]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      {/* Drawdown + rolling Sharpe */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="Combined Drawdown" subtitle="Underwater curve (% from peak)">
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={drawdownData} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="dd" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#f43f5e" stopOpacity={0.05} />
                  <stop offset="100%" stopColor="#f43f5e" stopOpacity={0.4} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-dim)" }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={44} tickFormatter={(v) => `${v}%`} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(244,63,94,0.3)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${Number(v)}%`, "Drawdown"]} />
              <Area type="monotone" dataKey="dd" stroke="#f43f5e" fill="url(#dd)" strokeWidth={1.5} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Rolling Sharpe (30d)" subtitle="Annualised, combined book">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={rollingSharpe} margin={{ left: 4, right: 12, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--text-dim)" }} minTickGap={40} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={34} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }} />
              <Line type="monotone" dataKey="sharpe" stroke={BOOK_COLOR.COMBINED} dot={false} strokeWidth={1.8} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      {/* Comparison grid */}
      <Panel title="Metric Comparison" subtitle="Every metric computed independently per engine + combined">
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr className="text-[var(--text-dim)] text-[0.7rem] uppercase tracking-wide">
                <th className="text-left font-medium py-2 pl-1">Metric</th>
                {ORDER.map((b) => (
                  <th key={b} className="text-right font-semibold py-2 pr-3" style={{ color: BOOK_COLOR[b] }}>
                    {BOOK_LABEL[b]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {SECTIONS.map((sec) => (
                <Fragment key={sec.title}>
                  <tr>
                    <td colSpan={5} className="pt-4 pb-1 pl-1 text-[0.68rem] uppercase tracking-wider text-[var(--accent)]/80 font-semibold">
                      {sec.title}
                    </td>
                  </tr>
                  {sec.rows.map((row) => (
                    <tr key={row.label} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="py-1.5 pl-1 text-[var(--text-secondary)]">{row.label}</td>
                      {ORDER.map((b) => {
                        const m = metrics[b];
                        const raw = m ? (m[row.key] as number) : undefined;
                        const tone = row.tone ? toneClass(raw) : "text-[var(--text-primary)]";
                        return (
                          <td key={b} className={`py-1.5 pr-3 text-right tabular-nums ${tone}`}>
                            {cellValue(m, row)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div className="glass rounded-xl p-3.5 border border-white/5">
      <div className="text-[0.62rem] uppercase tracking-wide text-[var(--text-dim)]">{label}</div>
      <div className={`text-lg md:text-xl font-bold mt-1 tabular-nums ${tone !== undefined ? toneClass(tone) : "text-[var(--text-primary)]"}`}>
        {value}
      </div>
    </div>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="glass rounded-xl p-4 border border-white/5">
      <div className="mb-3">
        <div className="text-sm font-semibold text-[var(--text-primary)]">{title}</div>
        {subtitle && <div className="text-[0.68rem] text-[var(--text-dim)]">{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}
