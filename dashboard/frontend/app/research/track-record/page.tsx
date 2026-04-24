"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { ArrowLeft, TrendingUp, TrendingDown, Target, ShieldAlert, Clock } from "lucide-react";
import { api, type TrackRecordPick, type TrackRecordSummary } from "@/lib/api";

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="glass" style={{ padding: "16px 20px", minWidth: 140 }}>
      <div style={{ fontSize: "0.65rem", textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-secondary)", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: "1.4rem", fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

const STATUS_CONFIG: Record<string, { bg: string; color: string; label: string }> = {
  TARGET_HIT: { bg: "rgba(0,224,150,0.12)", color: "#00e096", label: "Target Hit" },
  STOP_HIT: { bg: "rgba(255,71,87,0.12)", color: "#ff4757", label: "Stop Hit" },
  ACTIVE: { bg: "rgba(0,212,255,0.1)", color: "#00d4ff", label: "Active" },
  RUNNING: { bg: "rgba(0,212,255,0.1)", color: "#00d4ff", label: "Running" },
  EXPIRED: { bg: "rgba(58,74,107,0.3)", color: "#8899bb", label: "Expired" },
  ARCHIVED: { bg: "rgba(58,74,107,0.2)", color: "#6677aa", label: "Archived" },
  CLOSED: { bg: "rgba(245,158,11,0.12)", color: "#f59e0b", label: "Closed" },
};

export default function TrackRecordPage() {
  const [picks, setPicks] = useState<TrackRecordPick[]>([]);
  const [summary, setSummary] = useState<TrackRecordSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "swing" | "longterm">("all");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  useEffect(() => {
    setLoading(true);
    api.trackRecord(filter, 200)
      .then((res) => {
        setPicks(res.picks);
        setSummary(res.summary);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [filter]);

  const filtered = useMemo(() => {
    if (statusFilter === "ALL") return picks;
    return picks.filter((p) => p.status === statusFilter);
  }, [picks, statusFilter]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/research" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem" }}>
          <ArrowLeft size={16} /> Research
        </Link>
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Track Record</h1>
        <span style={{ fontSize: "0.7rem", padding: "2px 8px", borderRadius: 4, background: "rgba(0,212,255,0.1)", color: "var(--accent)" }}>
          Historical Performance
        </span>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div style={{ display: "flex", gap: 12, overflowX: "auto", paddingBottom: 4 }}>
          <StatCard label="Total Picks" value={String(summary.total_picks)} color="var(--text-primary)" />
          <StatCard label="Hit Rate" value={`${summary.hit_rate_pct}%`} sub={`${summary.target_hit}W / ${summary.stop_hit}L`} color={summary.hit_rate_pct >= 50 ? "#00e096" : "#ff4757"} />
          <StatCard label="Avg P&L" value={`${summary.avg_pnl_pct > 0 ? "+" : ""}${summary.avg_pnl_pct}%`} color={summary.avg_pnl_pct >= 0 ? "#00e096" : "#ff4757"} />
          <StatCard label="Best Trade" value={`+${summary.best_pnl_pct}%`} color="#00e096" />
          <StatCard label="Worst Trade" value={`${summary.worst_pnl_pct}%`} color="#ff4757" />
        </div>
      )}

      {/* Filters */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {(["all", "swing", "longterm"] as const).map((h) => (
          <button
            key={h}
            onClick={() => setFilter(h)}
            style={{
              padding: "5px 14px", borderRadius: 6, fontSize: "0.75rem", fontWeight: 600,
              cursor: "pointer", border: "1px solid",
              background: filter === h ? "rgba(0,212,255,0.15)" : "transparent",
              borderColor: filter === h ? "rgba(0,212,255,0.4)" : "rgba(255,255,255,0.08)",
              color: filter === h ? "#00d4ff" : "var(--text-secondary)",
            }}
          >
            {h === "all" ? "All" : h === "swing" ? "Swing" : "Long-Term"}
          </button>
        ))}
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.08)", margin: "0 4px" }} />
        {["ALL", "TARGET_HIT", "STOP_HIT", "ACTIVE", "EXPIRED"].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            style={{
              padding: "4px 10px", borderRadius: 5, fontSize: "0.68rem", fontWeight: 600,
              cursor: "pointer", border: "1px solid",
              background: statusFilter === s ? (STATUS_CONFIG[s]?.bg || "rgba(0,212,255,0.1)") : "transparent",
              borderColor: statusFilter === s ? (STATUS_CONFIG[s]?.color || "#00d4ff") + "55" : "rgba(255,255,255,0.06)",
              color: statusFilter === s ? (STATUS_CONFIG[s]?.color || "#00d4ff") : "var(--text-dim)",
            }}
          >
            {s === "ALL" ? "All" : STATUS_CONFIG[s]?.label || s}
          </button>
        ))}
      </div>

      {/* Table */}
      {loading ? (
        <div className="glass" style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>Loading track record...</div>
      ) : filtered.length === 0 ? (
        <div className="glass" style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>No picks found for the selected filters.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ minWidth: 900 }}>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Type</th>
                <th>Setup</th>
                <th>Entry</th>
                <th>SL</th>
                <th>Target</th>
                <th>Exit</th>
                <th>P&L %</th>
                <th>Days</th>
                <th>Conf.</th>
                <th>Status</th>
                <th>Date</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((pick) => {
                const sc = STATUS_CONFIG[pick.status] || STATUS_CONFIG.ACTIVE;
                const pnl = pick.pnl_pct;
                const targets = pick.targets || [];
                return (
                  <tr key={pick.id}>
                    <td>
                      <a
                        href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(pick.symbol.replace("NSE:", ""))}`}
                        target="_blank" rel="noopener noreferrer"
                        style={{ color: "var(--accent)", textDecoration: "none", fontWeight: 600, fontSize: "0.82rem" }}
                      >
                        {pick.symbol.replace("NSE:", "")}
                      </a>
                    </td>
                    <td style={{ fontSize: "0.72rem" }}>
                      <span style={{
                        padding: "2px 6px", borderRadius: 4, fontSize: "0.65rem", fontWeight: 600,
                        background: pick.agent_type === "SWING" ? "rgba(91,156,246,0.12)" : "rgba(240,192,96,0.12)",
                        color: pick.agent_type === "SWING" ? "#5b9cf6" : "#f0c060",
                      }}>
                        {pick.agent_type}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>{pick.setup || "—"}</td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>₹{pick.entry_price.toFixed(2)}</td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem", color: "#ff4757" }}>
                      {pick.stop_loss ? `₹${pick.stop_loss.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem", color: "#00e096" }}>
                      {targets.length > 0 ? `₹${Number(targets[0]).toFixed(2)}` : "—"}
                    </td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
                      {pick.exit_price ? `₹${pick.exit_price.toFixed(2)}` : pick.current_price ? `₹${pick.current_price.toFixed(2)}` : "—"}
                    </td>
                    <td style={{
                      fontWeight: 700, fontSize: "0.82rem",
                      color: pnl === null ? "var(--text-dim)" : pnl >= 0 ? "#00e096" : "#ff4757",
                    }}>
                      {pnl !== null ? `${pnl > 0 ? "+" : ""}${pnl}%` : "—"}
                    </td>
                    <td style={{ fontSize: "0.78rem", color: "var(--text-secondary)" }}>
                      {pick.days_held !== null ? `${pick.days_held}d` : "—"}
                    </td>
                    <td>
                      <div style={{
                        width: 32, height: 32, borderRadius: "50%", display: "grid", placeItems: "center",
                        fontSize: "0.65rem", fontWeight: 700,
                        background: `conic-gradient(${pick.confidence_score >= 70 ? "#00e096" : pick.confidence_score >= 50 ? "#f59e0b" : "#ff4757"} ${pick.confidence_score * 3.6}deg, rgba(255,255,255,0.05) 0deg)`,
                        color: "var(--text-primary)",
                      }}>
                        {pick.confidence_score.toFixed(0)}
                      </div>
                    </td>
                    <td>
                      <span style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        padding: "2px 8px", borderRadius: 4, fontSize: "0.65rem", fontWeight: 600,
                        background: sc.bg, color: sc.color,
                      }}>
                        {pick.status === "TARGET_HIT" && <Target size={10} />}
                        {pick.status === "STOP_HIT" && <ShieldAlert size={10} />}
                        {(pick.status === "ACTIVE" || pick.status === "RUNNING") && <TrendingUp size={10} />}
                        {pick.status === "EXPIRED" && <Clock size={10} />}
                        {sc.label}
                      </span>
                    </td>
                    <td style={{ fontSize: "0.72rem", color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                      {pick.created_at ? new Date(pick.created_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" }) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
