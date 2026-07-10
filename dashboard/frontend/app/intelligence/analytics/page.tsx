"use client";

import { useEffect, useMemo, useState } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell } from "recharts";
import { Analytics, WhatIfResult, BOOK_LABEL, BOOK_COLOR, fetchAnalytics, whatIf, fmtINR, fmtPct, fmtNum } from "@/lib/pil";

const ENGINES = ["SWING", "LONGTERM", "MOMENTUM"];

export default function AnalyticsPage() {
  const [data, setData] = useState<Analytics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [w, setW] = useState<Record<string, number>>({ SWING: 60, LONGTERM: 25, MOMENTUM: 15 });
  const [sim, setSim] = useState<WhatIfResult | null>(null);

  useEffect(() => { fetchAnalytics().then(setData).catch((e) => setErr(e instanceof Error ? e.message : "error")); }, []);
  useEffect(() => {
    const t = setTimeout(() => { whatIf(w).then(setSim).catch(() => {}); }, 250);
    return () => clearTimeout(t);
  }, [w]);

  const contribBar = useMemo(
    () => (data?.contribution.rows ?? []).map((r) => ({ book: BOOK_LABEL[r.book], key: r.book, pnl: r.pnl })),
    [data]
  );

  if (err) return <div className="glass rounded-xl p-6 border border-rose-500/30 text-sm text-rose-400">{err}</div>;
  if (!data) return <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading analytics…</div>;

  const div = data.diversification;

  return (
    <div className="flex flex-col gap-5">
      {/* Leaderboard */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Lead label="Top Contributor" book={data.contribution.top_contributor} />
        <Lead label="Highest Return" book={data.leaderboard.highest_return} />
        <Lead label="Lowest Drawdown" book={data.leaderboard.lowest_drawdown} />
        <Lead label="Highest Sharpe" book={data.leaderboard.highest_sharpe} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Contribution */}
        <Panel title="Return Contribution" subtitle="₹ P&L contributed by each engine">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={contribBar} margin={{ left: 8, right: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" />
              <XAxis dataKey="book" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={54} tickFormatter={(v) => fmtINR(v)} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [fmtINR(Number(v)), "P&L"]} />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                {contribBar.map((r) => <Cell key={r.key} fill={BOOK_COLOR[r.key]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        {/* Diversification */}
        <Panel title="Diversification Benefit" subtitle="Combined volatility vs weighted-average of standalone vols">
          <div className="grid grid-cols-2 gap-3 mb-4">
            <Metric label="Weighted-Avg Vol" value={fmtPct(div.weighted_avg_vol_pct)} />
            <Metric label="Combined Vol" value={fmtPct(div.combined_vol_pct)} accent />
            <Metric label="Benefit" value={fmtPct(div.diversification_benefit_pct)} accent />
            <Metric label="Div. Ratio" value={fmtNum(div.diversification_ratio, 2)} />
          </div>
          <div className="text-[0.72rem] text-[var(--text-secondary)]">
            {div.diversification_benefit_pct > 0
              ? `Diversifying across engines reduces portfolio volatility by ${div.diversification_benefit_pct.toFixed(1)} pts vs holding them in isolation.`
              : "Engines are moving together — limited diversification benefit right now."}
          </div>
        </Panel>
      </div>

      {/* What-if allocation simulator */}
      <Panel title="Allocation What-If" subtitle="Drag engine weights to simulate combined risk/return over history">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="flex flex-col gap-3">
            {ENGINES.map((e) => (
              <div key={e} className="flex items-center gap-3">
                <div className="w-24 text-sm" style={{ color: BOOK_COLOR[e] }}>{BOOK_LABEL[e]}</div>
                <input type="range" min={0} max={100} value={w[e]}
                  onChange={(ev) => setW({ ...w, [e]: Number(ev.target.value) })}
                  className="flex-1 accent-cyan-400" />
                <div className="w-12 text-right tabular-nums text-[var(--text-primary)]">{w[e]}%</div>
              </div>
            ))}
            <div className="text-[0.68rem] text-[var(--text-dim)]">Weights are normalised automatically.</div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Metric label="Ann. Return" value={sim ? fmtPct(sim.ann_return_pct) : "—"} accent />
            <Metric label="Ann. Vol" value={sim ? fmtPct(sim.ann_vol_pct) : "—"} />
            <Metric label="Sharpe" value={sim ? fmtNum(sim.sharpe) : "—"} accent />
            <Metric label="Max Drawdown" value={sim ? fmtPct(sim.max_drawdown_pct) : "—"} />
          </div>
        </div>
      </Panel>

      {/* Optimal allocation */}
      <Panel title="Optimal Allocation" subtitle="Historical grid-search — for insight, not an instruction to any engine">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <OptCard title="Max Sharpe" r={data.optimal.max_sharpe} />
          <OptCard title="Min Volatility" r={data.optimal.min_vol} />
          <OptCard title="Current Mix" r={data.optimal.current} />
        </div>
      </Panel>
    </div>
  );
}

function OptCard({ title, r }: { title: string; r: WhatIfResult | null }) {
  return (
    <div className="rounded-lg bg-white/[0.03] p-3 border border-white/5">
      <div className="text-[0.7rem] uppercase tracking-wide text-[var(--accent)]/80 font-semibold mb-2">{title}</div>
      {r ? (
        <>
          <div className="flex flex-col gap-1 mb-2">
            {ENGINES.map((e) => (
              <div key={e} className="flex justify-between text-xs">
                <span style={{ color: BOOK_COLOR[e] }}>{BOOK_LABEL[e]}</span>
                <span className="tabular-nums text-[var(--text-primary)]">{((r.weights[e] ?? 0) * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
          <div className="flex justify-between text-[0.7rem] text-[var(--text-secondary)] pt-2 border-t border-white/5">
            <span>Sharpe {fmtNum(r.sharpe)}</span>
            <span>Vol {fmtPct(r.ann_vol_pct)}</span>
          </div>
        </>
      ) : <div className="text-[var(--text-dim)] text-xs">insufficient history</div>}
    </div>
  );
}

function Lead({ label, book }: { label: string; book: string | null }) {
  return (
    <div className="glass rounded-xl p-3 border border-white/5">
      <div className="text-[0.6rem] uppercase tracking-wide text-[var(--text-dim)]">{label}</div>
      <div className="text-base font-bold mt-1" style={{ color: book ? BOOK_COLOR[book] : "var(--text-primary)" }}>
        {book ? BOOK_LABEL[book] : "—"}
      </div>
    </div>
  );
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg bg-white/[0.03] p-2.5">
      <div className="text-[0.58rem] uppercase text-[var(--text-dim)]">{label}</div>
      <div className={`text-base font-bold tabular-nums ${accent ? "text-[var(--accent)]" : "text-[var(--text-primary)]"}`}>{value}</div>
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
