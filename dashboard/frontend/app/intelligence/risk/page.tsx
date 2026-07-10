"use client";

import { useEffect, useState } from "react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell,
} from "recharts";
import { AlertTriangle } from "lucide-react";
import {
  Exposure, BOOK_LABEL, BOOK_COLOR, fetchExposure, fmtINR, fmtPct, fmtNum,
} from "@/lib/pil";

const SECTOR_PALETTE = ["#22d3ee", "#a78bfa", "#f59e0b", "#34d399", "#f472b6", "#60a5fa", "#fb7185", "#facc15", "#4ade80", "#c084fc"];

function corrColor(v: number | null): string {
  if (v === null) return "rgba(148,163,184,0.15)";
  // -1 (green, diversifying) → +1 (red, concentrated)
  const t = (v + 1) / 2;
  const r = Math.round(52 + t * (244 - 52));
  const g = Math.round(211 - t * (211 - 63));
  const b = Math.round(153 - t * (153 - 94));
  return `rgba(${r},${g},${b},0.85)`;
}

export default function RiskPage() {
  const [data, setData] = useState<Exposure | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchExposure().then(setData).catch((e) => setErr(e instanceof Error ? e.message : "error"));
  }, []);

  if (err) return <div className="glass rounded-xl p-6 border border-rose-500/30 text-sm text-rose-400">{err}</div>;
  if (!data) return <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading exposure…</div>;

  const engines = data.correlation.engines;

  return (
    <div className="flex flex-col gap-6">
      {/* Risk KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Kpi label="Portfolio Beta" value={fmtNum(data.portfolio_beta)} />
        <Kpi label="Diversification" value={fmtNum(data.diversification_score)} sub="0–1" />
        <Kpi label="Eff. Holdings" value={fmtNum(data.effective_holdings, 1)} />
        <Kpi label="Concentration (HHI)" value={fmtNum(data.hhi, 3)} />
        <Kpi label="Top-10 Share" value={fmtPct(data.top10_pct)} />
        <Kpi label="Cash" value={fmtPct(data.cash_pct)} />
      </div>

      {/* Warnings */}
      {data.warnings.length > 0 && (
        <div className="glass rounded-xl p-4 border border-amber-500/30">
          <div className="flex items-center gap-2 text-amber-400 text-sm font-semibold mb-2">
            <AlertTriangle size={15} /> Risk Warnings ({data.warnings.length})
          </div>
          <ul className="flex flex-col gap-1.5">
            {data.warnings.map((w, i) => (
              <li key={i} className="flex items-center gap-2 text-[0.8rem]">
                <span className={`px-1.5 py-0.5 rounded text-[0.6rem] font-bold ${
                  w.severity === "CRITICAL" ? "bg-rose-500/20 text-rose-300"
                  : w.severity === "WARN" ? "bg-amber-500/20 text-amber-300"
                  : "bg-sky-500/20 text-sky-300"}`}>{w.severity}</span>
                <span className="text-[var(--text-secondary)]">{w.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Sector exposure */}
        <Panel title="Sector Exposure" subtitle="% of deployed capital">
          <ResponsiveContainer width="100%" height={Math.max(200, data.by_sector.length * 32)}>
            <BarChart data={data.by_sector} layout="vertical" margin={{ left: 8, right: 24 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 10, fill: "var(--text-dim)" }} tickFormatter={(v) => `${v}%`} />
              <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${Number(v).toFixed(1)}%`, "Exposure"]} />
              <Bar dataKey="pct" radius={[0, 3, 3, 0]}>
                {data.by_sector.map((_, i) => <Cell key={i} fill={SECTOR_PALETTE[i % SECTOR_PALETTE.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        {/* Correlation matrix */}
        <Panel title="Engine Correlation" subtitle="Daily-return correlation — lower is better diversification">
          <div className="overflow-x-auto">
            <table className="text-xs border-collapse mx-auto">
              <thead>
                <tr>
                  <th className="p-2"></th>
                  {engines.map((e) => <th key={e} className="p-2 text-[var(--text-secondary)] font-medium">{BOOK_LABEL[e]}</th>)}
                </tr>
              </thead>
              <tbody>
                {engines.map((a) => (
                  <tr key={a}>
                    <td className="p-2 text-[var(--text-secondary)] font-medium text-right">{BOOK_LABEL[a]}</td>
                    {engines.map((c) => {
                      const v = data.correlation.matrix[a][c];
                      return (
                        <td key={c} className="p-1">
                          <div className="w-16 h-12 rounded grid place-items-center font-semibold text-slate-900"
                            style={{ background: corrColor(v) }}>
                            {v === null ? "—" : v.toFixed(2)}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* Market cap */}
        <Panel title="Market-Cap Exposure">
          <MiniBars buckets={data.by_market_cap} />
        </Panel>

        {/* Theme */}
        <Panel title="Theme Exposure">
          <MiniBars buckets={data.by_theme} />
        </Panel>
      </div>

      {/* Top holdings */}
      <Panel title="Top Holdings" subtitle={`${data.holdings.length} names · largest ${data.largest_holding?.symbol ?? "—"}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[560px]">
            <thead>
              <tr className="text-[var(--text-dim)] text-[0.7rem] uppercase tracking-wide">
                <th className="text-left py-2 pl-1">Symbol</th>
                <th className="text-left py-2">Sector</th>
                <th className="text-left py-2">Engines</th>
                <th className="text-right py-2">Value</th>
                <th className="text-right py-2 pr-2">% Deployed</th>
                <th className="text-right py-2 pr-2">% NAV</th>
              </tr>
            </thead>
            <tbody>
              {data.top10.map((h) => (
                <tr key={h.symbol} className="border-t border-white/5">
                  <td className="py-1.5 pl-1 font-semibold text-[var(--text-primary)]">{h.symbol}</td>
                  <td className="py-1.5 text-[var(--text-secondary)]">{h.sector}</td>
                  <td className="py-1.5">
                    <span className="flex gap-1">
                      {h.books.map((b) => (
                        <span key={b} className="text-[0.6rem] px-1 rounded" style={{ background: `${BOOK_COLOR[b]}22`, color: BOOK_COLOR[b] }}>
                          {BOOK_LABEL[b]}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">{fmtINR(h.value)}</td>
                  <td className="py-1.5 text-right tabular-nums pr-2">{fmtPct(h.pct)}</td>
                  <td className="py-1.5 text-right tabular-nums pr-2 text-[var(--text-secondary)]">{fmtPct(h.pct_nav)}</td>
                </tr>
              ))}
              {data.top10.length === 0 && (
                <tr><td colSpan={6} className="py-6 text-center text-[var(--text-dim)]">No open holdings</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function MiniBars({ buckets }: { buckets: { name: string; pct: number }[] }) {
  if (!buckets.length) return <div className="text-[var(--text-dim)] text-xs py-6 text-center">No data</div>;
  return (
    <div className="flex flex-col gap-2">
      {buckets.map((b, i) => (
        <div key={b.name} className="flex items-center gap-2 text-xs">
          <div className="w-24 text-[var(--text-secondary)] truncate">{b.name}</div>
          <div className="flex-1 h-3 rounded bg-white/5 overflow-hidden">
            <div className="h-full rounded" style={{ width: `${Math.min(b.pct, 100)}%`, background: SECTOR_PALETTE[i % SECTOR_PALETTE.length] }} />
          </div>
          <div className="w-12 text-right tabular-nums text-[var(--text-primary)]">{b.pct.toFixed(1)}%</div>
        </div>
      ))}
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="glass rounded-xl p-3 border border-white/5">
      <div className="text-[0.6rem] uppercase tracking-wide text-[var(--text-dim)]">{label}</div>
      <div className="text-base md:text-lg font-bold mt-0.5 tabular-nums text-[var(--text-primary)]">{value}</div>
      {sub && <div className="text-[0.55rem] text-[var(--text-dim)]">{sub}</div>}
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
