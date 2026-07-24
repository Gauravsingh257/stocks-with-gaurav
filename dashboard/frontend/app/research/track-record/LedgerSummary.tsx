"use client";

/**
 * LedgerSummary — PR3.
 *
 * Survivorship-free headline stats from the immutable ledger
 * (/api/research/track-record/ledger). Unlike the legacy summary (which reads
 * mutable, recyclable rows and silently drops timed-out ideas), this counts
 * EVERY published recommendation — so the win rate is over all resolved ideas
 * including EXPIRED ones. Honest, not inflated. Fails silently.
 */

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api, type LedgerStats } from "@/lib/api";

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="glass" style={{ padding: "12px 16px", minWidth: 120 }}>
      <div style={{ fontSize: "0.6rem", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-secondary)", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.25rem", fontWeight: 700, color: color || "var(--text-primary)" }}>{value}</div>
      {sub && <div style={{ fontSize: "0.66rem", color: "var(--text-dim)", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

export function LedgerSummary({ horizon = "all" }: { horizon?: "swing" | "longterm" | "all" }) {
  const [s, setS] = useState<LedgerStats | null>(null);

  useEffect(() => {
    let alive = true;
    api.trackRecordLedger(horizon).then((r) => alive && setS(r.stats)).catch(() => {});
    return () => { alive = false; };
  }, [horizon]);

  if (!s || !s.available || !s.total_published) return null;

  const wr = s.win_rate_pct;
  const wrColor = wr == null ? "var(--text-secondary)" : wr >= 50 ? "#00e096" : wr >= 40 ? "#f59e0b" : "#ff4757";

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>
        <ShieldCheck size={15} color="#00e096" />
        <span style={{ fontWeight: 700, fontSize: "0.82rem" }}>Survivorship-Free Track Record</span>
        <span style={{ fontSize: "0.64rem", color: "var(--text-dim)" }}>· immutable ledger · every idea counted</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Tile label="Win Rate" value={wr == null ? "—" : `${wr}%`} sub="of all resolved" color={wrColor} />
        <Tile label="Published" value={String(s.total_published ?? 0)} />
        <Tile label="Resolved" value={String(s.resolved ?? 0)} sub={`${s.open ?? 0} open`} />
        <Tile label="Target / Stop" value={`${s.target_hit ?? 0} / ${s.stop_hit ?? 0}`} />
        <Tile label="Expired" value={String(s.expired ?? 0)} sub="timed out" />
        <Tile label="Expectancy" value={s.expectancy_r == null ? "—" : `${s.expectancy_r}R`} color={(s.expectancy_r ?? 0) >= 0 ? "#00e096" : "#ff4757"} />
        <Tile label="Avg Win / Loss" value={`${s.avg_win_pct ?? "—"}% / ${s.avg_loss_pct ?? "—"}%`} />
        <Tile label="Avg Hold" value={s.avg_holding_days == null ? "—" : `${s.avg_holding_days}d`} />
      </div>
      {s.note && <div style={{ fontSize: "0.66rem", color: "var(--text-dim)", marginTop: 8 }}>{s.note}</div>}
    </div>
  );
}

export default LedgerSummary;
