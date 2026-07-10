"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, FileText } from "lucide-react";
import { BOOK_LABEL, STATUS_COLOR, fetchReport, generateReport, fmtINR, fmtPct } from "@/lib/pil";

type ReportResp = { kind: string; period: string; payload: Record<string, unknown>; html?: string | null };

export default function ReportsPage() {
  const [kind, setKind] = useState<"daily" | "monthly">("daily");
  const [report, setReport] = useState<ReportResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (k: "daily" | "monthly") => {
    setLoading(true); setErr(null);
    try { setReport(await fetchReport(k)); }
    catch (e) { setErr(e instanceof Error ? e.message : "error"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(kind); }, [kind, load]);

  async function regen() {
    setBusy(true); setErr(null);
    try { setReport(await generateReport(kind)); }
    catch (e) { setErr(e instanceof Error ? e.message : "error"); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex gap-1 glass rounded-lg p-1 border border-white/5">
          {(["daily", "monthly"] as const).map((k) => (
            <button key={k} onClick={() => setKind(k)}
              className={`px-3 py-1 text-xs rounded-md capitalize ${kind === k ? "bg-[var(--accent)]/20 text-[var(--accent)]" : "text-[var(--text-secondary)]"}`}>
              {k}
            </button>
          ))}
        </div>
        <button onClick={regen} disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50">
          <RefreshCw size={13} className={busy ? "animate-spin" : ""} /> Generate Now
        </button>
      </div>

      {err && <div className="glass rounded-xl p-4 border border-rose-500/30 text-sm text-rose-400">{err}</div>}
      {loading && <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading report…</div>}

      {!loading && report && kind === "monthly" && report.html && (
        <div className="glass rounded-xl overflow-hidden border border-white/5">
          <iframe title="monthly-report" srcDoc={report.html} className="w-full" style={{ height: "80vh", border: "none" }} />
        </div>
      )}

      {!loading && report && kind === "daily" && <DailyView payload={report.payload} period={report.period} />}
    </div>
  );
}

function DailyView({ payload, period }: { payload: Record<string, unknown>; period: string }) {
  const p = payload as any;
  const ps = p.portfolio_summary || {};
  const health = p.portfolio_health || {};
  return (
    <div className="flex flex-col gap-4">
      <div className="glass rounded-xl p-4 border border-white/5">
        <div className="flex items-center gap-2 mb-3">
          <FileText size={15} className="text-[var(--accent)]" />
          <span className="font-semibold text-[var(--text-primary)]">Daily Report — {period}</span>
          <span className="text-[0.7rem] text-[var(--text-dim)] ml-auto">Regime: {p.market_regime}</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Stat label="Value" value={fmtINR(ps.portfolio_value)} />
          <Stat label="Today" value={fmtPct(ps.today_return_pct)} />
          <Stat label="Total" value={fmtPct(ps.total_return_pct)} />
          <Stat label="Cash" value={fmtINR(ps.cash)} />
          <Stat label="Open/Pending" value={`${ps.open_positions ?? 0}/${ps.pending_positions ?? 0}`} />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <ListPanel title={`New Entries (${(p.new_entries || []).length})`}
          items={(p.new_entries || []).map((e: any) => `${e.symbol} · ${BOOK_LABEL[e.book] ?? e.book}`)} />
        <ListPanel title={`Exits (${(p.exits || []).length})`}
          items={(p.exits || []).map((e: any) => `${e.symbol} · ${fmtPct(e.profit_loss_pct)}`)} />
        <ListPanel title={`Pending (${(p.pending || []).length})`}
          items={(p.pending || []).map((e: any) => `${e.symbol} · ${BOOK_LABEL[e.book] ?? e.book}`)} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="glass rounded-xl p-4 border border-white/5">
          <div className="text-sm font-semibold mb-3">Portfolio Health</div>
          <div className="flex flex-wrap gap-2">
            {["COMBINED", "SWING", "LONGTERM", "MOMENTUM"].map((b) => health[b] && (
              <span key={b} className="text-[0.72rem] px-2 py-1 rounded-lg" style={{
                background: `${STATUS_COLOR[health[b].status]}22`, color: STATUS_COLOR[health[b].status] }}>
                {BOOK_LABEL[b]}: {health[b].overall} {health[b].status}
              </span>
            ))}
          </div>
        </div>
        <div className="glass rounded-xl p-4 border border-white/5">
          <div className="text-sm font-semibold mb-3">Sector Exposure</div>
          {(p.sector_exposure || []).slice(0, 6).map((s: any) => (
            <div key={s.name} className="flex justify-between text-[0.75rem] py-0.5">
              <span className="text-[var(--text-secondary)]">{s.name}</span>
              <span className="tabular-nums">{s.pct?.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>

      {(p.risk_warnings || []).length > 0 && (
        <div className="glass rounded-xl p-4 border border-amber-500/30">
          <div className="text-amber-400 text-sm font-semibold mb-2">Risk Warnings</div>
          {(p.risk_warnings || []).map((w: any, i: number) => (
            <div key={i} className="text-[0.78rem] text-[var(--text-secondary)]">{w.message}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] p-2.5">
      <div className="text-[0.58rem] uppercase text-[var(--text-dim)]">{label}</div>
      <div className="text-sm font-bold tabular-nums text-[var(--text-primary)]">{value}</div>
    </div>
  );
}

function ListPanel({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="glass rounded-xl p-4 border border-white/5">
      <div className="text-sm font-semibold mb-2">{title}</div>
      {items.length ? (
        <ul className="flex flex-col gap-1">
          {items.slice(0, 10).map((it, i) => <li key={i} className="text-[0.75rem] text-[var(--text-secondary)]">{it}</li>)}
        </ul>
      ) : <div className="text-[var(--text-dim)] text-xs">None</div>}
    </div>
  );
}
