"use client";
import { Activity, AlertTriangle, CheckCircle2, Gauge } from "lucide-react";
import { fmt, type ExecutionQuality, type ShortCoveringSignal } from "./types";

function formatSignalTime(value?: string | null): string {
  if (!value) return "—";
  const s = String(value).trim();
  // Already HH:MM:SS
  if (/^\d{2}:\d{2}:\d{2}$/.test(s)) return s;
  const d = new Date(s);
  if (!Number.isNaN(d.getTime())) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }
  return s;
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "bad";
}) {
  const color =
    tone === "good" ? "var(--success)" :
    tone === "bad" ? "var(--danger)" :
    "var(--text-primary)";

  return (
    <div
      style={{
        padding: "10px 12px",
        borderRadius: 8,
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: "0.92rem", fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

export function ExecutionQualityPanel({
  quality,
  scSignals,
}: {
  quality?: ExecutionQuality;
  scSignals: ShortCoveringSignal[];
}) {
  const hasQuality = !!quality;
  const topSignal = scSignals.length > 0
    ? [...scSignals].sort((a, b) => b.score - a.score)[0]
    : null;
  const topSignalTime = quality?.top_signal_time || topSignal?.signal_time || null;
  const lastExitTime = quality?.last_oi_sc_exit_time || null;
  const lastExitOutcome = quality?.last_oi_sc_outcome || null;
  const lastExitSymbol = quality?.last_oi_sc_symbol || null;

  return (
    <div className="glass" style={{ padding: 20, minHeight: 200 }}>
      <div
        style={{
          fontSize: "0.7rem",
          color: "var(--text-secondary)",
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          fontWeight: 600,
          marginBottom: 14,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <Gauge size={14} />
        EXECUTION QUALITY
      </div>

      {!hasQuality ? (
        <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 28 }}>
          Execution quality stats unavailable
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 md:gap-2.5">
          <Metric label="Trades Today" value={String(quality.total_trades_today)} />
          <Metric label="Index Trades" value={String(quality.index_trades_today)} />
          <Metric
            label="Win Rate"
            value={`${fmt(quality.win_rate_today, 1)}%`}
            tone={quality.win_rate_today >= 55 ? "good" : quality.win_rate_today < 40 ? "bad" : "neutral"}
          />
          <Metric
            label="Net R"
            value={`${quality.net_r_today >= 0 ? "+" : ""}${fmt(quality.net_r_today, 2)}R`}
            tone={quality.net_r_today > 0 ? "good" : quality.net_r_today < 0 ? "bad" : "neutral"}
          />
          <Metric label="Avg R / Trade" value={fmt(quality.avg_r_today, 3)} />
          <Metric label="OI-SC Trades" value={String(quality.oi_sc_trades_today)} />
          <Metric label="OI-SC MFE (R)" value={fmt(quality.oi_sc_mfe_r_avg, 3)} tone="good" />
          <Metric label="OI-SC MAE (R)" value={fmt(quality.oi_sc_mae_r_avg, 3)} tone={quality.oi_sc_mae_r_avg < -0.5 ? "bad" : "neutral"} />
        </div>
      )}

      <div
        style={{
          marginTop: 14,
          borderTop: "1px solid rgba(255,255,255,0.08)",
          paddingTop: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={{ fontSize: "0.68rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
          Actionable Now
        </div>
        {topSignal ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-primary)", fontSize: "0.78rem", flexWrap: "wrap" }}>
            <Activity size={14} color="var(--accent)" />
            Top OI-SC: <strong>{topSignal.tradingsymbol}</strong> ({topSignal.score}/10)
            <span style={{ color: "var(--text-dim)" }}>
              at {formatSignalTime(topSignalTime)}
            </span>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-dim)", fontSize: "0.78rem" }}>
            <AlertTriangle size={14} />
            No active high-quality OI-SC setup right now
          </div>
        )}
        {lastExitTime ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-primary)", fontSize: "0.78rem", flexWrap: "wrap" }}>
            <Activity size={14} />
            Last OI-SC Exit: <strong>{formatSignalTime(lastExitTime)}</strong>
            {lastExitSymbol && <span style={{ color: "var(--text-dim)" }}>({lastExitSymbol})</span>}
            <span className={`badge ${lastExitOutcome === "TARGET_HIT" ? "badge-win" : "badge-paper"}`} style={{ fontSize: "0.62rem" }}>
              {lastExitOutcome === "TARGET_HIT" ? "PROFIT HIT" : "SL HIT"}
            </span>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-dim)", fontSize: "0.78rem" }}>
            <AlertTriangle size={14} />
            No OI-SC SL/target hit recorded yet
          </div>
        )}
        {quality && quality.net_r_today > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--success)", fontSize: "0.78rem" }}>
            <CheckCircle2 size={14} />
            Session quality positive — protect gains, avoid forcing entries
          </div>
        )}
      </div>
    </div>
  );
}

