"use client";

import type { RunningTradeMonitorItem } from "@/lib/api";

interface Props {
  items: RunningTradeMonitorItem[];
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(2);
}

function barColor(color: RunningTradeMonitorItem["progress_color"]) {
  if (color === "red") return "#ff4d6d";
  if (color === "green") return "#00ff88";
  return "#ffd700";
}

export function RunningTradesMonitor({ items }: Props) {
  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Running Trades Monitor</div>
      {items.length === 0 ? (
        <div style={{ color: "var(--text-secondary)" }}>No running trades are active from research recommendations.</div>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {items.map(item => (
            <div key={item.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                <strong>{item.symbol}</strong>
                <span style={{ color: item.profit_loss >= 0 ? "#00ff88" : "#ff4d6d", fontSize: "0.8rem" }}>
                  PnL {fmt(item.profit_loss)}
                </span>
              </div>
              <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", marginBottom: 6 }}>
                Entry {fmt(item.entry_price)} - Current {fmt(item.current_price)} - Target {fmt(item.targets[item.targets.length - 1])}
              </div>
              <div style={{ height: 8, borderRadius: 999, background: "rgba(255,255,255,0.08)", overflow: "hidden", marginBottom: 6 }}>
                <div style={{ width: `${Math.round(item.progress * 100)}%`, height: "100%", background: barColor(item.progress_color), transition: "width 0.3s ease" }} />
              </div>
              <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)" }}>
                Drawdown {fmt(item.drawdown)} | To Target {fmt(item.distance_to_target)} | To SL {fmt(item.distance_to_stop_loss)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
