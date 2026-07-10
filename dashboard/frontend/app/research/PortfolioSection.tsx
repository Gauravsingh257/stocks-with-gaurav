"use client";

import { useState } from "react";
import type { PortfolioPosition, PortfolioJournalStats } from "@/lib/api";

interface PortfolioSectionProps {
  title: string;
  positions: PortfolioPosition[];
  count: number;            // LIVE (ACTIVE) count
  pending?: number;         // armed (awaiting entry) count
  max: number;
  journalStats: PortfolioJournalStats | null;
  horizon: "SWING" | "LONGTERM" | "MOMENTUM";
  // "live" = active trades (+ closed history); "awaiting" = only armed
  // (PENDING) ideas. Each is its own tab so the page stays short.
  viewMode?: "live" | "awaiting";
}

// Portfolio glyph per book.
function horizonIcon(h: string): string {
  return h === "SWING" ? "📊" : h === "LONGTERM" ? "🏦" : "🚀";
}

// Momentum re-scoring classification → colour.
function classBadge(cls: string | null | undefined) {
  if (!cls) return null;
  const map: Record<string, { c: string; bg: string }> = {
    ELITE: { c: "#00ff88", bg: "rgba(0,255,136,0.14)" },
    GOOD: { c: "#00d18c", bg: "rgba(0,209,140,0.12)" },
    WEAK: { c: "#f0c060", bg: "rgba(240,192,96,0.12)" },
    REPLACE: { c: "#ff6b88", bg: "rgba(255,77,109,0.12)" },
  };
  const s = map[cls] || { c: "#aaa", bg: "rgba(170,170,170,0.1)" };
  return (
    <span style={{ fontSize: "0.6rem", fontWeight: 700, color: s.c, background: s.bg,
      padding: "1px 6px", borderRadius: 999, border: `1px solid ${s.c}33` }}>{cls}</span>
  );
}

function fmt(v: number | null | undefined, dec = 2) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(dec);
}

// ── CSV export (client-side, no backend) ──────────────────────────────────────
const CSV_COLUMNS: { key: keyof PortfolioPosition; label: string }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "horizon", label: "Horizon" },
  { key: "status", label: "Status" },
  { key: "direction", label: "Direction" },
  { key: "entry_price", label: "Entry" },
  { key: "current_price", label: "CMP" },
  { key: "stop_loss", label: "StopLoss" },
  { key: "target_1", label: "Target1" },
  { key: "target_2", label: "Target2" },
  { key: "profit_loss_pct", label: "PnL_%" },
  { key: "profit_loss", label: "PnL_pts" },
  { key: "drawdown_pct", label: "Drawdown_%" },
  { key: "high_since_entry", label: "HighSinceEntry" },
  { key: "low_since_entry", label: "LowSinceEntry" },
  { key: "days_held", label: "DaysHeld" },
  { key: "confidence_score", label: "Confidence" },
  { key: "exit_price", label: "ExitPrice" },
  { key: "exit_reason", label: "ExitReason" },
  { key: "created_at", label: "AddedAt" },
  { key: "updated_at", label: "UpdatedAt" },
  { key: "closed_at", label: "ClosedAt" },
];

function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  // Quote when the value could break CSV parsing (comma, quote, newline).
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadPositionsCsv(positions: PortfolioPosition[], horizon: string) {
  const stamp = new Date().toISOString().slice(0, 10); // YYYY-MM-DD (daily file)
  const header = CSV_COLUMNS.map((c) => c.label).join(",");
  const lines = positions.map((p) =>
    CSV_COLUMNS.map((c) => csvCell(p[c.key])).join(",")
  );
  const csv = [header, ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${horizon.toLowerCase()}-portfolio-${stamp}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
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
    PENDING:     { label: "⏳ AWAITING ENTRY", color: "#f0c060", bg: "rgba(240,192,96,0.12)" },
    ACTIVE:      { label: "● ACTIVE",      color: "#00d18c", bg: "rgba(0,209,140,0.12)" },
    TARGET_HIT:  { label: "✓ TARGET HIT",  color: "#00ff88", bg: "rgba(0,255,136,0.12)" },
    STOP_HIT:    { label: "✕ STOP HIT",    color: "#ff4d6d", bg: "rgba(255,77,109,0.12)" },
    CLOSED:      { label: "CLOSED",         color: "#888",    bg: "rgba(136,136,136,0.10)" },
    PARTIAL_EXIT:{ label: "PARTIAL",        color: "#f59e0b", bg: "rgba(245,158,11,0.12)" },
    EXPIRED:     { label: "EXPIRED",        color: "#888",    bg: "rgba(136,136,136,0.10)" },
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

function PositionCard({ pos, rank }: { pos: PortfolioPosition & { classification?: string | null; quality_score?: number | null }; rank: number }) {
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
            {/* symbol may or may not already carry the NSE: prefix — normalize so
                we never render the doubled "NSE:NSE:" form. */}
            NSE:{pos.symbol.replace(/^NSE:/, "")}
          </a>
          <span style={{ fontSize: "0.65rem", color: "var(--text-secondary)" }}>
            Added {fmtDate(pos.created_at)} · {pos.days_held}d held
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {classBadge(pos.classification)}
          {typeof pos.quality_score === "number" && (
            <span style={{ fontSize: "0.6rem", color: "var(--text-secondary)" }}>
              Q{pos.quality_score.toFixed(0)}
            </span>
          )}
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

// Armed idea awaiting its entry to be traded through. NO P&L, NO days-held —
// it is not a live position yet. Shows the planned entry + how far CMP is from it.
function PendingCard({ pos, rank }: { pos: PortfolioPosition; rank: number }) {
  const entry = pos.entry_price;
  const cmp = pos.current_price ?? pos.arm_ref_price ?? entry;
  const gapPct = entry > 0 ? ((cmp - entry) / entry) * 100 : 0;
  const isPullback = (pos.arm_ref_price ?? cmp) >= entry;
  const waitMsg = isPullback
    ? `Waiting for a pullback to ₹${fmt(entry)} (CMP ${gapPct >= 0 ? "+" : ""}${fmt(gapPct, 1)}% above)`
    : `Waiting for a breakout through ₹${fmt(entry)} (CMP ${fmt(gapPct, 1)}% away)`;
  return (
    <div className="glass" style={{ padding: "12px 16px", marginBottom: 8, borderLeft: "3px solid #f0c060", opacity: 0.92 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text-secondary)" }}>#{rank}</span>
          <a href={tvUrl(pos.symbol)} target="_blank" rel="noopener noreferrer"
             style={{ fontWeight: 700, fontSize: "0.95rem", color: "var(--accent)", textDecoration: "none" }}>
            NSE:{pos.symbol.replace(/^NSE:/, "")}
          </a>
          <span style={{ fontSize: "0.65rem", color: "var(--text-secondary)" }}>Armed {fmtDate(pos.created_at)}</span>
        </div>
        {statusBadge(pos.status)}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: 8, fontSize: "0.78rem" }}>
        <div><div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>PLANNED ENTRY</div><div style={{ fontWeight: 600 }}>₹{fmt(entry)}</div></div>
        <div><div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>CMP</div><div style={{ fontWeight: 600 }}>₹{fmt(cmp)}</div></div>
        <div><div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>STOP LOSS</div><div style={{ fontWeight: 600, color: "#ff4d6d" }}>₹{fmt(pos.stop_loss)}</div></div>
        {pos.target_1 && <div><div style={{ color: "var(--text-secondary)", fontSize: "0.65rem" }}>TARGET 1</div><div style={{ fontWeight: 600, color: "#00d18c" }}>₹{fmt(pos.target_1)}</div></div>}
      </div>
      <div style={{ marginTop: 8, fontSize: "0.66rem", color: "#f0c060" }}>{waitMsg} · no P&amp;L until it triggers</div>
    </div>
  );
}

export function PortfolioSection({ title, positions, count, pending, max, journalStats, horizon, viewMode = "live" }: PortfolioSectionProps) {
  const [showClosed, setShowClosed] = useState(false);

  const activePositions = positions.filter(p => p.status === "ACTIVE");
  const pendingPositions = positions.filter(p => p.status === "PENDING");
  const closedPositions = positions.filter(p => !["ACTIVE", "PENDING", "EXPIRED"].includes(p.status));
  const pendingCount = pending ?? pendingPositions.length;

  // ── AWAITING ENTRY view: only armed ideas (no P&L, not a live position) ──
  if (viewMode === "awaiting") {
    return (
      <div className="glass" style={{ padding: 16, position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
          <h2 style={{ margin: 0, fontSize: "1.05rem" }}>
            {horizonIcon(horizon)} {title} — Awaiting Entry
          </h2>
          <span style={{
            fontSize: "0.66rem", fontWeight: 700, color: "#f0c060",
            background: "rgba(240,192,96,0.12)", border: "1px solid rgba(240,192,96,0.35)",
            padding: "2px 9px", borderRadius: 999,
          }}>
            ⏳ {pendingCount}
          </span>
          <span style={{ fontSize: "0.68rem", color: "var(--text-secondary)" }}>
            armed, not yet triggered · excluded from P&amp;L &amp; track record
          </span>
        </div>
        {pendingPositions.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            No armed ideas awaiting entry right now.
          </div>
        ) : (
          pendingPositions.map((pos, i) => (
            <PendingCard key={pos.id} pos={pos} rank={i + 1} />
          ))
        )}
      </div>
    );
  }

  // ── LIVE view: active trades (+ closed history) ──
  return (
    <div className="glass" style={{ padding: 16, position: "relative" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
            {horizonIcon(horizon)} {title}
          </h2>
          <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>
            {Math.min(count, max)} Active · {Math.min(count + pendingCount, max)}/{max} slots
            {journalStats && journalStats.total_trades > 0 && (() => {
              const s = journalStats;
              const MIN_SAMPLE = 10; // don't headline stats off a tiny sample
              if (s.total_trades < MIN_SAMPLE) {
                return <> · {s.total_trades} completed · building track record</>;
              }
              const tgt = s.target_hits ?? 0;
              const stop = s.stop_hits ?? 0;
              const cut = (s.structure_exits ?? 0) + (s.other_exits ?? 0);
              // Headline win rate = unique-setup rate (repeat re-entries of the
              // same setup collapsed). Labeled '*' with the basis + realized rate
              // in the tooltip so it is disclosed, not passed off as all-trades.
              const collapsed = s.repeat_reentries_collapsed ?? 0;
              const winRate = collapsed > 0 && s.unique_hit_rate_pct !== undefined
                ? s.unique_hit_rate_pct : s.hit_rate_pct;
              const basis = collapsed > 0
                ? `Per unique setup: ${s.unique_wins}/${s.unique_trades} = ${s.unique_hit_rate_pct}% (excludes ${collapsed} repeat re-entries of the same setup, now prevented by the re-entry guard). Realized across all ${s.total_trades} closed trades: ${s.hit_rate_pct}%.`
                : `${s.wins}/${s.total_trades} closed trades net positive.`;
              return (
                <> · <span title={basis} style={{ color: winRate >= 50 ? "#00d18c" : "#f0c060", fontWeight: 700, borderBottom: "1px dotted currentColor", cursor: "help" }}>{winRate}% win rate{collapsed > 0 ? "*" : ""}</span> · {s.total_trades} completed · <span style={{ color: "#00d18c" }}>{tgt} target</span> / <span style={{ color: "var(--text-secondary)" }}>{cut} cut early</span> / <span style={{ color: "#ff4d6d" }}>{stop} stopped</span> · Total return: <span style={{ color: plColor(s.total_pnl_pct), fontWeight: 700 }}>{s.total_pnl_pct > 0 ? "+" : ""}{s.total_pnl_pct}%</span></>
              );
            })()}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={() => downloadPositionsCsv(positions, horizon)}
            disabled={positions.length === 0}
            title={`Download ${positions.length} ${horizon} position(s) as CSV`}
            style={{
              fontSize: "0.68rem", fontWeight: 600,
              color: positions.length === 0 ? "var(--text-dim)" : "var(--accent)",
              background: "rgba(0,212,255,0.08)",
              border: "1px solid rgba(0,212,255,0.25)",
              padding: "3px 10px", borderRadius: 999,
              cursor: positions.length === 0 ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", gap: 4,
            }}
          >
            ⬇ CSV
          </button>
          {(() => {
            const used = count + pendingCount;   // a slot is held by active OR armed
            return (
              <div style={{
                fontSize: "0.7rem", fontWeight: 700,
                color: used >= max ? "#ff4d6d" : "#00d18c",
                background: used >= max ? "rgba(255,77,109,0.1)" : "rgba(0,209,140,0.1)",
                padding: "3px 10px", borderRadius: 999,
              }}>
                {used >= max ? "FULL" : `${max - used} SLOTS OPEN`}
              </div>
            );
          })()}
        </div>
      </div>

      {activePositions.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem", lineHeight: 1.55 }}>
          No live positions{pendingCount > 0 ? " — check the Awaiting Entry tab" : " · promote a name from the decision feed or run a scan"}.
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
