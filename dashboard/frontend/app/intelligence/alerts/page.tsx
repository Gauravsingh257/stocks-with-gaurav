"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bell, RefreshCw, CheckCircle2 } from "lucide-react";
import { Alert, BOOK_LABEL, fetchAlerts, evaluateAlerts } from "@/lib/pil";

const SEV_STYLE: Record<string, { bg: string; text: string; icon: typeof Bell }> = {
  CRITICAL: { bg: "bg-rose-500/10 border-rose-500/30", text: "text-rose-300", icon: AlertTriangle },
  WARN: { bg: "bg-amber-500/10 border-amber-500/30", text: "text-amber-300", icon: AlertTriangle },
  INFO: { bg: "bg-sky-500/10 border-sky-500/30", text: "text-sky-300", icon: Bell },
};

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try { setAlerts((await fetchAlerts(true)).alerts); }
    catch (e) { setErr(e instanceof Error ? e.message : "error"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function evaluate() {
    setBusy(true); setErr(null);
    try { await evaluateAlerts(); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "error"); }
    finally { setBusy(false); }
  }

  const grouped = { CRITICAL: [] as Alert[], WARN: [] as Alert[], INFO: [] as Alert[] };
  for (const a of alerts) (grouped[a.severity as keyof typeof grouped] ?? grouped.INFO).push(a);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-[var(--text-secondary)]">
          {alerts.length} active alert{alerts.length !== 1 ? "s" : ""}
          {grouped.CRITICAL.length > 0 && <span className="text-rose-400 ml-2">· {grouped.CRITICAL.length} critical</span>}
        </div>
        <button onClick={evaluate} disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50">
          <RefreshCw size={13} className={busy ? "animate-spin" : ""} /> Re-evaluate
        </button>
      </div>

      {err && <div className="glass rounded-xl p-4 border border-rose-500/30 text-sm text-rose-400">{err}</div>}
      {loading && <div className="text-[var(--text-secondary)] text-sm py-20 text-center">Loading alerts…</div>}

      {!loading && alerts.length === 0 && (
        <div className="glass rounded-xl p-10 border border-emerald-500/20 text-center">
          <CheckCircle2 size={32} className="text-emerald-400 mx-auto mb-2" />
          <div className="text-[var(--text-primary)] font-medium">All clear</div>
          <div className="text-[var(--text-dim)] text-sm">No portfolio thresholds are currently breached.</div>
        </div>
      )}

      {(["CRITICAL", "WARN", "INFO"] as const).map((sev) => grouped[sev].length > 0 && (
        <div key={sev} className="flex flex-col gap-2">
          {grouped[sev].map((a) => {
            const s = SEV_STYLE[a.severity] ?? SEV_STYLE.INFO;
            const Icon = s.icon;
            return (
              <div key={a.id} className={`glass rounded-xl p-3.5 border flex items-start gap-3 ${s.bg}`}>
                <Icon size={16} className={`${s.text} mt-0.5 shrink-0`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[0.58rem] font-bold px-1.5 py-0.5 rounded ${s.text}`}>{a.severity}</span>
                    <span className="text-[0.6rem] px-1.5 py-0.5 rounded bg-white/5 text-[var(--text-dim)]">{a.type}</span>
                    <span className="text-[0.6rem] text-[var(--text-dim)]">{BOOK_LABEL[a.book] ?? a.book}</span>
                  </div>
                  <div className="text-sm text-[var(--text-secondary)] mt-1">{a.message}</div>
                </div>
                <div className="text-[0.6rem] text-[var(--text-dim)] shrink-0">{a.ts?.slice(5, 16)}</div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
