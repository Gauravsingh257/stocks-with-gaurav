"use client";

import { useState } from "react";
import type { PortfolioPosition, PortfolioJournalStats } from "@/lib/api";

interface PortfolioSectionProps {
  title: string;
  positions: PortfolioPosition[];
  count: number;
  max: number;
  journalStats: PortfolioJournalStats | null;
  horizon: "SWING" | "LONGTERM";
}

function fmt(v: number | null | undefined, dec = 2) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(dec);
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    const s = String(iso).replace(" ", "T");
    const norm = s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z";
    return new Date(norm).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return "-";
  }
}

function tvUrl(symbol: string) {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent("NSE:" + symbol.replace("NSE:", ""))}&interval=D`;
}

function plColor(v: number) {
  return v > 0 ? "#00d18c" : v < 0 ? "#ff4d6d" : "var(--text-secondary)";
}

function statusBadge(status: string) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    ACTIVE:      { label: "● ACTIVE",      color: "#00d18c", bg: "rgba(0,209,140,0.12)" },
    TARGET_HIT:  { label: "✓ TARGET HIT",  color: "#00ff88", bg: "rgba(0,255,136,0.12)" },
    STOP_HIT:    { label: "✕ STOP HIT",    color: "#ff4d6d", bg: "rgba(255,77,109,0.12)" },
    CLOSED:      { label: "CLOSED",         color: "#888",    bg: "rgba(136,136,136,0.10)" },
    PARTIAL_EXIT:{ label: "PARTIAL",        color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
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

function PositionCard({ pos, rank }: { pos: PortfolioPosition; rank: number }) {
  const entry = pos.entry_price;
  const cmp = pos.current_price ?? entry;
  const sl = pos.stop_loss;
  const t1 = pos.target_1;
  const t2 = pos.target_2;
  const maxTarget = t2 ?? t1 ?? entry * 1.20;
  const risk = Math.abs(entry - sl);
  const reward = maxTarget - entry;
  const rr = risk > 0 ? (reward / risk).toFixed(1) : "-";

  // Progress bar: SL (0%) → Entry (baseline) → Target (100%)
  const range = maxTarget - sl;
  const progress = range > 0 ? Math.min(Math.max(((cmp - sl) / range) * 100, 0), 100) : 50;
  const entryPct = range > 0 ? ((entry - sl) / range) * 100 : 50;

  return (
    <div
      className="glass"
      style={{
        padding: "14px 16px", marginBottom: 8,
        borderLeft: pos.status === "ACTIVE" ? "3px solid #00d18c" : "3px solid #555",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>#{rank}</span>
          <a
            href={tvUrl(pos.symbol)}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--accent)", textDecoration: "none" }}
          >
            NSE:{pos.symbol}
          </a>
          <span style={{ fontSize: "0.65rem", color: "var(--text-secondary)" }}>
            Added {fmtDate(pos.created_at)} · {pos.days_held}d held
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {statusBadge(pos.status)}
          <a href={tvUrl(pos.symbol)} target="_blank" rel="noopener noreferrer"
             style={{ fontSize: "0.65rem", color: "var(--text-secondary)" }}>
            Chart ↗
          </a>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: 8, fontSize: "0.78rem" }}>
        <div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>ENTRY</div>
          <div style={{ fontWeight: 600 }}>₹{fmt(entry)}</div>
        </div>
        <div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>CMP</div>
          <div style={{ fontWeight: 600, color: plColor(pos.profit_loss) }}>₹{fmt(cmp)}</div>
        </div>
        <div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>STOP LOSS</div>
          <div style={{ fontWeight: 600, color: "#ff4d6d" }}>₹{fmt(sl)}</div>
        </div>
        {t1 && (
          <div>
            <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>TARGET 1</div>
            <div style={{ fontWeight: 600, color: "#00d18c" }}>₹{fmt(t1)}</div>
          </div>
        )}
        {t2 && t2 !== t1 && (
          <div>
            <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>TARGET 2</div>
            <div style={{ fontWeight: 600, color: "#00d18c" }}>₹{fmt(t2)}</div>
          </div>
        )}
        <div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>P&L</div>
          <div style={{ fontWeight: 700, color: plColor(pos.profit_loss_pct) }}>
            {pos.profit_loss_pct > 0 ? "+" : ""}{fmt(pos.profit_loss_pct)}%
          </div>
        </div>
        <div>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>R:R</div>
          <div style={{ fontWeight: 600 }}>1:{rr}</div>
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ marginTop: 10, position: "relative", height: 6, background: "rgba(255,255,255,0.05)", borderRadius: 3 }}>
        {/* Entry marker */}
        <div style={{
          position: "absolute", left: `${entryPct}%`, top: -2, width: 2, height: 10,
          background: "var(--text-secondary)", borderRadius: 1, zIndex: 2,
        }} />
        {/* Fill */}
        <div style={{
          height: "100%", borderRadius: 3,
          width: `${progress}%`,
          background: pos.profit_loss_pct >= 0
            ? "linear-gradient(90deg, #00d18c, #00ff88)"
            : "linear-gradient(90deg, #ff4d6d, #ff6b88)",
          transition: "width 0.3s ease",
        }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.6rem", color: "var(--text-secondary)", marginTop: 2 }}>
        <span>SL: ₹{fmt(sl)}</span>
        <span>Entry: ₹{fmt(entry)}</span>
        <span>Target: ₹{fmt(maxTarget)}</span>
      </div>
    </div>
  );
}

export function PortfolioSection({ title, positions, count, max, journalStats, horizon }: PortfolioSectionProps) {
  const [showClosed, setShowClosed] = useState(false);

  const activePositions = positions.filter(p => p.status === "ACTIVE");
  const closedPositions = positions.filter(p => p.status !== "ACTIVE");

  return (
    <div className="glass" style={{ padding: 16, position: "relative" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
            {horizon === "SWING" ? "📊" : "🏦"} {title}
          </h2>
          <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
            {count}/{max} Active Slots
            {journalStats && journalStats.total_trades > 0 && (
              <> · {journalStats.total_trades} completed · {journalStats.hit_rate_pct}% hit rate · Avg P&L: {journalStats.avg_pnl_pct > 0 ? "+" : ""}{journalStats.avg_pnl_pct}%</>
            )}
          </span>
        </div>
        <div style={{
          fontSize: "0.7rem", fontWeight: 700,
          color: count >= max ? "#ff4d6d" : "#00d18c",
          background: count >= max ? "rgba(255,77,109,0.1)" : "rgba(0,209,140,0.1)",
          padding: "3px 10px", borderRadius: 999,
        }}>
          {count >= max ? "FULL" : `${max - count} SLOTS OPEN`}
        </div>
      </div>

      {activePositions.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
          No active {horizon.toLowerCase()} positions. Run a scan to populate.
        </div>
      )}

      {activePositions.map((pos, i) => (
        <PositionCard key={pos.id} pos={pos} rank={i + 1} />
      ))}

      {closedPositions.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <button
            onClick={() => setShowClosed(!showClosed)}
            style={{
              fontSize: "0.72rem", color: "var(--text-secondary)",
              background: "none", border: "none", cursor: "pointer",
              textDecoration: "underline",
            }}
          >
            {showClosed ? "Hide" : "Show"} {closedPositions.length} closed position{closedPositions.length > 1 ? "s" : ""}
          </button>
          {showClosed && closedPositions.map((pos, i) => (
            <PositionCard key={pos.id} pos={pos} rank={activePositions.length + i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
