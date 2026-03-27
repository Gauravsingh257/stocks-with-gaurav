"use client";

import type { ResearchAggregatePerformance } from "@/lib/api";

interface Props {
  data: ResearchAggregatePerformance | null;
}

function fmt(v: number | null | undefined, dec = 1) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(dec);
}

function plColor(v: number) {
  return v > 0 ? "#00d18c" : v < 0 ? "#ff4d6d" : "var(--text-secondary)";
}

export function PerformanceOverview({ data }: Props) {
  if (!data) return null;

  const statCards: { label: string; value: string; color: string; sub?: string }[] = [
    {
      label: "Total Picks",
      value: String(data.total_recommendations),
      color: "var(--accent)",
      sub: `${data.active} active · ${data.resolved} resolved`,
    },
    {
      label: "Hit Rate",
      value: `${fmt(data.hit_rate_pct)}%`,
      color: data.hit_rate_pct >= 50 ? "#00d18c" : data.hit_rate_pct >= 30 ? "#f0c060" : "#ff4d6d",
      sub: `${data.target_hit} targets hit · ${data.stop_hit} stopped out`,
    },
    {
      label: "Avg Closed P&L",
      value: `${data.avg_closed_pnl_pct >= 0 ? "+" : ""}${fmt(data.avg_closed_pnl_pct, 2)}%`,
      color: plColor(data.avg_closed_pnl_pct),
      sub: `Total: ${data.total_pnl_pct >= 0 ? "+" : ""}${fmt(data.total_pnl_pct, 2)}%`,
    },
    {
      label: "Avg Open P&L",
      value: `${data.avg_open_pnl_pct >= 0 ? "+" : ""}${fmt(data.avg_open_pnl_pct, 2)}%`,
      color: plColor(data.avg_open_pnl_pct),
      sub: `${data.active} positions running`,
    },
    {
      label: "Best Trade",
      value: data.best_trade ? `${data.best_trade.symbol}` : "-",
      color: "#00d18c",
      sub: data.best_trade ? `+${fmt(data.best_trade.pnl_pct, 2)}%` : undefined,
    },
    {
      label: "Worst Trade",
      value: data.worst_trade ? `${data.worst_trade.symbol}` : "-",
      color: "#ff4d6d",
      sub: data.worst_trade ? `${fmt(data.worst_trade.pnl_pct, 2)}%` : undefined,
    },
    {
      label: "Avg Days Held",
      value: `${fmt(data.avg_days_held, 0)}d`,
      color: "var(--text-primary)",
    },
    {
      label: "Total Scans Run",
      value: String(data.swing_scans + data.longterm_scans),
      color: "var(--accent)",
      sub: `${data.swing_scans} swing · ${data.longterm_scans} long-term`,
    },
  ];

  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <span>📊 Research Performance</span>
        <span style={{
          fontSize: "0.68rem", color: "var(--text-secondary)",
          background: "rgba(255,255,255,0.05)", padding: "2px 8px", borderRadius: 999,
        }}>
          All-time
        </span>
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
        gap: 10,
      }}>
        {statCards.map(({ label, value, color, sub }) => (
          <div key={label} style={{
            padding: "12px 14px",
            background: "rgba(255,255,255,0.03)",
            border: "1px solid var(--border-muted)",
            borderRadius: 10,
          }}>
            <div style={{
              fontSize: "0.65rem", color: "var(--text-secondary)",
              textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 4,
            }}>
              {label}
            </div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color }}>
              {value}
            </div>
            {sub && (
              <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginTop: 2 }}>
                {sub}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
