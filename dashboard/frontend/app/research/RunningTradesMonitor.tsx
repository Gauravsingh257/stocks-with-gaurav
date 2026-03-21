"use client";

import { useState } from "react";
import type { RunningTradeMonitorItem } from "@/lib/api";

interface Props {
  items: RunningTradeMonitorItem[];
}

function fmt(v: number | null | undefined, dec = 2) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(dec);
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch { return "-"; }
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true });
  } catch { return "-"; }
}

function tvUrl(symbol: string) {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=D`;
}

function statusBadge(status: string) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    RUNNING:    { label: "● LIVE",       color: "#00d18c", bg: "rgba(0,209,140,0.12)" },
    TARGET_HIT: { label: "✓ TARGET HIT", color: "#00ff88", bg: "rgba(0,255,136,0.12)" },
    STOP_HIT:   { label: "✕ STOP HIT",  color: "#ff4d6d", bg: "rgba(255,77,109,0.12)" },
    CLOSED:     { label: "CLOSED",       color: "#888",    bg: "rgba(136,136,136,0.10)" },
  };
  const s = map[status] ?? { label: status, color: "#aaa", bg: "rgba(170,170,170,0.1)" };
  return (
    <span style={{
      fontSize: "0.68rem", fontWeight: 700, letterSpacing: "0.04em",
      color: s.color, background: s.bg,
      padding: "2px 8px", borderRadius: 999,
      border: `1px solid ${s.color}33`,
    }}>
      {s.label}
    </span>
  );
}

function plColor(v: number) {
  return v > 0 ? "#00d18c" : v < 0 ? "#ff4d6d" : "var(--text-secondary)";
}

interface TradeCardProps {
  item: RunningTradeMonitorItem;
  rank: number;
}

function TradeCard({ item, rank }: TradeCardProps) {
  const [expanded, setExpanded] = useState(false);

  const entry = item.entry_price;
  const cmp = item.current_price;
  const sl = item.stop_loss;
  const targets = item.targets;
  const maxTarget = targets.length > 0 ? targets[targets.length - 1] : entry * 1.2;

  // Progress bar: 0% = entry, 100% = max target, red zone below entry
  const range = maxTarget - sl;
  const entryPct = range > 0 ? ((entry - sl) / range) * 100 : 50;
  const cmpPct = range > 0 ? Math.max(0, Math.min(100, ((cmp - sl) / range) * 100)) : 50;
  const t1Pct = targets[0] && range > 0 ? ((targets[0] - sl) / range) * 100 : null;
  const t2Pct = targets[1] && range > 0 ? ((targets[1] - sl) / range) * 100 : null;

  const gainPct = item.profit_loss_pct;
  const ddPct = item.drawdown_pct;

  return (
    <div style={{
      border: `1px solid ${item.status === "TARGET_HIT" ? "rgba(0,255,136,0.25)" : item.status === "STOP_HIT" ? "rgba(255,77,109,0.2)" : "var(--border)"}`,
      borderRadius: 12,
      background: "rgba(255,255,255,0.02)",
      overflow: "hidden",
    }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--border-muted)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)", fontWeight: 600, minWidth: 20 }}>#{rank}</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: "0.95rem", letterSpacing: "0.02em" }}>{item.symbol}</div>
            <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)", marginTop: 1 }}>
              Recommended {fmtDate(item.created_at)} · {item.days_held}d held
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {statusBadge(item.status)}
          <a href={tvUrl(item.symbol)} target="_blank" rel="noopener noreferrer"
            style={{ fontSize: "0.72rem", color: "#5b9cf6", textDecoration: "none", fontWeight: 600 }}>
            Chart ↗
          </a>
        </div>
      </div>

      {/* Key stats grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 0, borderBottom: "1px solid var(--border-muted)" }}>
        {[
          { label: "Entry", value: fmt(entry), color: "var(--text-primary)" },
          { label: "CMP (Live)", value: fmt(cmp), color: gainPct >= 0 ? "#00d18c" : "#ff4d6d" },
          { label: "Stop Loss", value: fmt(sl), color: "#ff4d6d" },
          { label: "Target", value: fmt(maxTarget), color: "#00d18c" },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ padding: "10px 14px", borderRight: "1px solid var(--border-muted)" }}>
            <div style={{ fontSize: "0.66rem", color: "var(--text-secondary)", marginBottom: 3, textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
            <div style={{ fontSize: "0.9rem", fontWeight: 700, color }}>{value}</div>
          </div>
        ))}
      </div>

      {/* P&L and drawdown row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 0, borderBottom: "1px solid var(--border-muted)" }}>
        <div style={{ padding: "8px 14px", borderRight: "1px solid var(--border-muted)" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: 2, textTransform: "uppercase" }}>P&L (pts)</div>
          <div style={{ fontSize: "0.85rem", fontWeight: 700, color: plColor(item.profit_loss) }}>
            {item.profit_loss >= 0 ? "+" : ""}{fmt(item.profit_loss)}
          </div>
        </div>
        <div style={{ padding: "8px 14px", borderRight: "1px solid var(--border-muted)" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: 2, textTransform: "uppercase" }}>P&L %</div>
          <div style={{ fontSize: "0.85rem", fontWeight: 700, color: plColor(gainPct) }}>
            {gainPct >= 0 ? "+" : ""}{fmt(gainPct, 2)}%
          </div>
        </div>
        <div style={{ padding: "8px 14px", borderRight: "1px solid var(--border-muted)" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: 2, textTransform: "uppercase" }}>High Since Entry</div>
          <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#f0c060" }}>{fmt(item.high_since_entry)}</div>
        </div>
        <div style={{ padding: "8px 14px" }}>
          <div style={{ fontSize: "0.65rem", color: "var(--text-secondary)", marginBottom: 2, textTransform: "uppercase" }}>Low Since Entry</div>
          <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#aaa" }}>{fmt(item.low_since_entry)}</div>
        </div>
      </div>

      {/* Progress bar with SL / Entry / T1 / T2 markers */}
      <div style={{ padding: "12px 16px 8px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.64rem", color: "var(--text-secondary)", marginBottom: 5 }}>
          <span>SL {fmt(sl)}</span>
          {t1Pct && <span style={{ color: "#00d18c" }}>T1 {fmt(targets[0])}</span>}
          {t2Pct && <span style={{ color: "#00d18c" }}>T2 {fmt(targets[1])}</span>}
          <span style={{ color: "#00ff88" }}>Target {fmt(maxTarget)}</span>
        </div>
        <div style={{ position: "relative", height: 10, borderRadius: 999, background: "rgba(255,255,255,0.07)", overflow: "visible" }}>
          {/* Red zone: SL to Entry */}
          <div style={{
            position: "absolute", left: 0, width: `${entryPct}%`,
            height: "100%", background: "rgba(255,77,109,0.25)", borderRadius: "999px 0 0 999px",
          }} />
          {/* Progress fill */}
          <div style={{
            position: "absolute", left: 0, width: `${cmpPct}%`,
            height: "100%",
            background: gainPct >= 0
              ? "linear-gradient(90deg, rgba(255,77,109,0.4) 0%, rgba(0,209,140,0.8) 100%)"
              : "rgba(255,77,109,0.6)",
            borderRadius: 999, transition: "width 0.4s ease",
          }} />
          {/* Entry marker */}
          <div style={{ position: "absolute", left: `${entryPct}%`, top: -3, width: 2, height: 16, background: "#ffffff88", transform: "translateX(-50%)" }} />
          {/* T1 marker */}
          {t1Pct && (
            <div style={{ position: "absolute", left: `${t1Pct}%`, top: -3, width: 2, height: 16, background: "#00d18c99", transform: "translateX(-50%)" }} />
          )}
          {/* T2 marker */}
          {t2Pct && (
            <div style={{ position: "absolute", left: `${t2Pct}%`, top: -3, width: 2, height: 16, background: "#00ff8866", transform: "translateX(-50%)" }} />
          )}
          {/* CMP dot */}
          <div style={{
            position: "absolute", left: `${cmpPct}%`, top: "50%",
            width: 12, height: 12, borderRadius: "50%",
            background: gainPct >= 0 ? "#00d18c" : "#ff4d6d",
            border: "2px solid #fff",
            transform: "translate(-50%, -50%)",
            boxShadow: `0 0 6px ${gainPct >= 0 ? "#00d18c" : "#ff4d6d"}`,
            transition: "left 0.4s ease",
          }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.63rem", color: "var(--text-secondary)", marginTop: 4 }}>
          <span>← SL</span>
          <span style={{ color: gainPct >= 0 ? "#00d18c" : "#ff4d6d", fontWeight: 600 }}>
            CMP {fmt(cmp)} ({gainPct >= 0 ? "+" : ""}{fmt(gainPct, 1)}%)
          </span>
          <span>Target →</span>
        </div>
      </div>

      {/* Expandable stats */}
      <div style={{ padding: "4px 16px 12px" }}>
        <button
          onClick={() => setExpanded(v => !v)}
          style={{
            fontSize: "0.7rem", color: "var(--accent)", background: "none", border: "none",
            cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 4,
          }}
        >
          {expanded ? "▲ Hide details" : "▼ More details"}
        </button>
        {expanded && (
          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
            {[
              { label: "Drawdown %", value: `${fmt(ddPct, 2)}%`, color: ddPct < -3 ? "#ff4d6d" : "var(--text-secondary)" },
              { label: "Dist. to T1", value: fmt(item.distance_to_target), color: "var(--text-secondary)" },
              { label: "Dist. to SL", value: fmt(item.distance_to_stop_loss), color: "#ff4d6d" },
              { label: "Days Held", value: `${item.days_held}d`, color: "var(--text-secondary)" },
              { label: "Last Updated", value: fmtTime(item.updated_at), color: "var(--text-secondary)" },
              { label: "Recommended", value: fmtDate(item.created_at), color: "var(--text-secondary)" },
            ].map(({ label, value, color }) => (
              <div key={label} style={{ padding: "8px 10px", background: "rgba(255,255,255,0.03)", borderRadius: 8 }}>
                <div style={{ fontSize: "0.63rem", color: "var(--text-secondary)", marginBottom: 2, textTransform: "uppercase" }}>{label}</div>
                <div style={{ fontSize: "0.82rem", fontWeight: 600, color }}>{value}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function RunningTradesMonitor({ items }: Props) {
  const active = items.filter(i => i.status === "RUNNING");
  const closed = items.filter(i => i.status !== "RUNNING");

  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div style={{ fontWeight: 600 }}>
          Running Trades Monitor
          {active.length > 0 && (
            <span style={{ marginLeft: 8, fontSize: "0.7rem", color: "#00d18c", background: "rgba(0,209,140,0.12)", padding: "2px 8px", borderRadius: 999, fontWeight: 700 }}>
              {active.length} LIVE
            </span>
          )}
        </div>
        <div style={{ fontSize: "0.68rem", color: "var(--text-secondary)" }}>
          Auto-refreshes every 5 min · Prices from NSE via yfinance
        </div>
      </div>

      {items.length === 0 ? (
        <div style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>
          No running trades yet. Run a swing or long-term scan — tracked positions appear here automatically.
        </div>
      ) : (
        <>
          {active.length > 0 && (
            <div style={{ display: "grid", gap: 12, marginBottom: closed.length > 0 ? 20 : 0 }}>
              {active.map((item, i) => <TradeCard key={item.id} item={item} rank={i + 1} />)}
            </div>
          )}
          {closed.length > 0 && (
            <>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: 10, letterSpacing: "0.06em", textTransform: "uppercase" }}>
                Completed / Closed
              </div>
              <div style={{ display: "grid", gap: 10 }}>
                {closed.map((item, i) => <TradeCard key={item.id} item={item} rank={i + 1} />)}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
