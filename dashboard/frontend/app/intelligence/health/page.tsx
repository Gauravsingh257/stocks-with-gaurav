"use client";

import { useEffect, useState } from "react";
import { HealthMap, BookHealth, BOOK_LABEL, STATUS_COLOR, fetchHealth } from "@/lib/pil";

const ORDER = ["COMBINED", "SWING", "LONGTERM", "MOMENTUM"];
const FACTOR_LABEL: Record<string, string> = {
  quality: "Quality", risk: "Risk", drawdown: "Drawdown", momentum: "Momentum",
  concentration: "Concentration", diversification: "Diversification", maturity: "Maturity",
  liquidity: "Liquidity", replacement_pressure: "Replacement",
};

export default function HealthPage() {
  const [data, setData] = useState<HealthMap | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { fetchHealth().then(setData).catch((e) => setErr(e instanceof Error ? e.message : "error")); }, []);

  if (err) return <div className="glass rounded-xl p-6 border border-rose-500/30 text-sm text-rose-400">{err}</div>;
  if (!data) return <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading health…</div>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {ORDER.map((b) => data[b] && <HealthCard key={b} book={b} h={data[b] as BookHealth} />)}
    </div>
  );
}

function Gauge({ value, status }: { value: number; status: string }) {
  const color = STATUS_COLOR[status] || "#94a3b8";
  const r = 42; const circ = 2 * Math.PI * r;
  const off = circ * (1 - Math.min(value, 100) / 100);
  return (
    <div className="relative w-28 h-28">
      <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90">
        <circle cx="50" cy="50" r={r} fill="none" stroke="rgba(148,163,184,0.15)" strokeWidth="8" />
        <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="8"
          strokeDasharray={circ} strokeDashoffset={off} strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 grid place-items-center flex-col">
        <div className="text-2xl font-bold tabular-nums" style={{ color }}>{value.toFixed(0)}</div>
        <div className="text-[0.55rem] font-semibold tracking-wide" style={{ color }}>{status}</div>
      </div>
    </div>
  );
}

function HealthCard({ book, h }: { book: string; h: BookHealth }) {
  const factors = Object.entries(h.sub_scores);
  return (
    <div className="glass rounded-xl p-4 border border-white/5">
      <div className="flex items-center gap-4">
        <Gauge value={h.overall} status={h.status} />
        <div className="flex-1">
          <div className="font-semibold text-[var(--text-primary)] mb-1">{BOOK_LABEL[book]}</div>
          <div className="text-[0.72rem] text-[var(--text-secondary)]">
            Best: <span className="text-emerald-400">{FACTOR_LABEL[h.best_factor] ?? h.best_factor}</span> ·
            Weakest: <span className="text-rose-400"> {FACTOR_LABEL[h.worst_factor] ?? h.worst_factor}</span>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 mt-4">
        {factors.map(([k, v]) => (
          <div key={k} className="flex items-center gap-2">
            <div className="w-24 text-[0.7rem] text-[var(--text-secondary)] truncate">{FACTOR_LABEL[k] ?? k}</div>
            <div className="flex-1 h-1.5 rounded bg-white/5 overflow-hidden">
              <div className="h-full rounded" style={{
                width: `${Math.min(v, 100)}%`,
                background: v >= 70 ? "#34d399" : v >= 45 ? "#f59e0b" : "#f43f5e",
              }} />
            </div>
            <div className="w-8 text-right text-[0.68rem] tabular-nums text-[var(--text-primary)]">{v.toFixed(0)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
