"use client";

import { useEffect, useState } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell } from "recharts";
import { AlertTriangle, Save, Wallet } from "lucide-react";
import { Allocation, BOOK_LABEL, BOOK_COLOR, fetchAllocation, setAllocationTargets, fetchPilConfig, setBookCapital, fmtINR, fmtPct } from "@/lib/pil";

const ENGINES = ["SWING", "LONGTERM", "MOMENTUM"];

export default function AllocationPage() {
  const [data, setData] = useState<Allocation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetchAllocation().then((d) => {
      setData(d);
      setEdit(Object.fromEntries(ENGINES.map((e) => [e, Math.round((d.targets[e] ?? 0) * 100)])));
    }).catch((e) => setErr(e instanceof Error ? e.message : "error"));
  }, []);

  async function save() {
    setSaving(true); setSaved(false);
    try {
      const d = await setAllocationTargets(edit);
      setData(d);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  if (err) return <div className="glass rounded-xl p-6 border border-rose-500/30 text-sm text-rose-400">{err}</div>;
  if (!data) return <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading allocation…</div>;

  const chart = data.rows.map((r) => ({
    book: BOOK_LABEL[r.book], key: r.book,
    Current: Number((r.current_weight * 100).toFixed(1)),
    Target: Number((r.target_weight * 100).toFixed(1)),
  }));
  const editTotal = ENGINES.reduce((s, e) => s + (edit[e] || 0), 0);

  return (
    <div className="flex flex-col gap-5">
      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Kpi label="Total Capital" value={fmtINR(data.total_value)} />
        <Kpi label="Rebalance Needed" value={data.rebalance_needed ? "YES" : "NO"} tone={data.rebalance_needed ? "warn" : "good"} />
        <Kpi label="Cash to Rebalance" value={fmtINR(data.cash_required_to_rebalance)} />
        <Kpi label="Max Drift" value={fmtPct(data.max_drift * 100, 0)} />
      </div>

      {data.warnings.length > 0 && (
        <div className="glass rounded-xl p-4 border border-amber-500/30">
          <div className="flex items-center gap-2 text-amber-400 text-sm font-semibold mb-2">
            <AlertTriangle size={15} /> Allocation Warnings
          </div>
          <ul className="flex flex-col gap-1">
            {data.warnings.map((w, i) => (
              <li key={i} className="text-[0.8rem] text-[var(--text-secondary)]">{w.message}</li>
            ))}
          </ul>
        </div>
      )}

      <CapitalEditor />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Current vs Target chart */}
        <Panel title="Current vs Target Allocation">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chart} margin={{ left: 8, right: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" />
              <XAxis dataKey="book" tick={{ fontSize: 11, fill: "var(--text-secondary)" }} />
              <YAxis tick={{ fontSize: 10, fill: "var(--text-dim)" }} width={40} tickFormatter={(v) => `${v}%`} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(34,211,238,0.3)", borderRadius: 8, fontSize: 12 }}
                formatter={(v) => [`${Number(v)}%`, ""]} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="Current" radius={[3, 3, 0, 0]}>
                {chart.map((r) => <Cell key={r.key} fill={BOOK_COLOR[r.key]} />)}
              </Bar>
              <Bar dataKey="Target" radius={[3, 3, 0, 0]} fillOpacity={0.4}>
                {chart.map((r) => <Cell key={r.key} fill={BOOK_COLOR[r.key]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        {/* Target editor */}
        <Panel title="Set Target Weights" subtitle="Adjusts PIL accounting targets only — never an engine">
          <div className="flex flex-col gap-3">
            {ENGINES.map((e) => (
              <div key={e} className="flex items-center gap-3">
                <div className="w-24 text-sm" style={{ color: BOOK_COLOR[e] }}>{BOOK_LABEL[e]}</div>
                <input type="range" min={0} max={100} value={edit[e] ?? 0}
                  onChange={(ev) => setEdit({ ...edit, [e]: Number(ev.target.value) })}
                  className="flex-1 accent-cyan-400" />
                <div className="w-12 text-right tabular-nums text-[var(--text-primary)]">{edit[e] ?? 0}%</div>
              </div>
            ))}
            <div className="flex items-center justify-between pt-2 border-t border-white/5">
              <span className="text-[0.72rem] text-[var(--text-dim)]">
                Sum {editTotal}% · normalised on save
              </span>
              <button onClick={save} disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50">
                <Save size={13} /> {saving ? "Saving…" : saved ? "Saved ✓" : "Save Targets"}
              </button>
            </div>
          </div>
        </Panel>
      </div>

      {/* Detail table */}
      <Panel title="Rebalancing Plan">
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr className="text-[var(--text-dim)] text-[0.7rem] uppercase tracking-wide">
                <th className="text-left py-2 pl-1">Engine</th>
                <th className="text-right py-2">Current Value</th>
                <th className="text-right py-2">Current</th>
                <th className="text-right py-2">Target</th>
                <th className="text-right py-2">Deviation</th>
                <th className="text-right py-2">Required</th>
                <th className="text-right py-2 pr-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.book} className="border-t border-white/5">
                  <td className="py-2 pl-1 font-semibold" style={{ color: BOOK_COLOR[r.book] }}>{BOOK_LABEL[r.book]}</td>
                  <td className="py-2 text-right tabular-nums">{fmtINR(r.current_value)}</td>
                  <td className="py-2 text-right tabular-nums">{(r.current_weight * 100).toFixed(1)}%</td>
                  <td className="py-2 text-right tabular-nums text-[var(--text-secondary)]">{(r.target_weight * 100).toFixed(1)}%</td>
                  <td className={`py-2 text-right tabular-nums ${Math.abs(r.deviation) > data.max_drift ? "text-amber-400" : "text-[var(--text-secondary)]"}`}>
                    {(r.deviation * 100).toFixed(1)}%
                  </td>
                  <td className="py-2 text-right tabular-nums">{fmtINR(r.required_delta)}</td>
                  <td className="py-2 text-right pr-2">
                    <span className={`text-[0.6rem] px-1.5 py-0.5 rounded font-bold ${
                      r.action === "ADD" ? "bg-emerald-500/20 text-emerald-300"
                      : r.action === "TRIM" ? "bg-rose-500/20 text-rose-300"
                      : "bg-white/10 text-[var(--text-dim)]"}`}>{r.action}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function CapitalEditor() {
  const [cap, setCap] = useState<Record<string, string>>({ SWING: "", LONGTERM: "", MOMENTUM: "" });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchPilConfig().then((c) => {
      setCap({
        SWING: String(c.capital.SWING ?? ""),
        LONGTERM: String(c.capital.LONGTERM ?? ""),
        MOMENTUM: String(c.capital.MOMENTUM ?? ""),
      });
    }).catch(() => {});
  }, []);

  async function save() {
    setSaving(true); setSaved(false); setErr(null);
    try {
      await setBookCapital({
        SWING: Number(cap.SWING), LONGTERM: Number(cap.LONGTERM), MOMENTUM: Number(cap.MOMENTUM),
      });
      setSaved(true);
      setTimeout(() => { window.location.reload(); }, 700);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="glass rounded-xl p-4 border border-white/5">
      <div className="flex items-center gap-2 mb-1">
        <Wallet size={15} className="text-[var(--accent)]" />
        <div className="text-sm font-semibold text-[var(--text-primary)]">Book Capital</div>
      </div>
      <div className="text-[0.68rem] text-[var(--text-dim)] mb-3">
        Set each book&apos;s real ₹ capital — drives every ₹ metric. Saved live (no redeploy).
      </div>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
        {["SWING", "LONGTERM", "MOMENTUM"].map((b) => (
          <div key={b}>
            <label className="text-[0.62rem] uppercase tracking-wide" style={{ color: BOOK_COLOR[b] }}>{BOOK_LABEL[b]} (₹)</label>
            <input type="number" min={0} value={cap[b]}
              onChange={(e) => setCap({ ...cap, [b]: e.target.value })}
              className="w-full mt-1 px-2 py-1.5 rounded-lg bg-white/5 border border-white/10 text-sm text-[var(--text-primary)] tabular-nums" />
          </div>
        ))}
        <button onClick={save} disabled={saving}
          className="flex items-center justify-center gap-1.5 px-3 py-2 text-xs rounded-lg bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50">
          <Save size={13} /> {saving ? "Saving…" : saved ? "Saved ✓" : "Save Capital"}
        </button>
      </div>
      {err && <div className="text-rose-400 text-xs mt-2">{err}</div>}
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" }) {
  const color = tone === "warn" ? "text-amber-400" : tone === "good" ? "text-emerald-400" : "text-[var(--text-primary)]";
  return (
    <div className="glass rounded-xl p-3.5 border border-white/5">
      <div className="text-[0.62rem] uppercase tracking-wide text-[var(--text-dim)]">{label}</div>
      <div className={`text-lg md:text-xl font-bold mt-1 tabular-nums ${color}`}>{value}</div>
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
