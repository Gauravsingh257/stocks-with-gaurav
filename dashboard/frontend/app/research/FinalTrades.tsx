"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { ExternalLink, Eye, EyeOff, Flame, LineChart } from "lucide-react";
import type { ResearchDecisionCard } from "@/lib/api";
import MarketMonitoringEmpty from "@/components/MarketMonitoringEmpty";
import AddToWatchlistButton from "@/components/AddToWatchlistButton";

function cleanSymbol(symbol: string): string {
  return symbol.replace(/^NSE:/i, "").replace(/\.NS$/i, "");
}
function setupLabel(setup: string | null | undefined): string {
  const raw = String(setup || "").trim();
  if (!raw) return "SMC confirmed";
  if (raw === "MOMENTUM_FALLBACK") return "Momentum breakout";
  if (raw.startsWith("SMC_SWING")) return "SMC swing";
  if (raw.startsWith("SMC_LONGTERM")) return "SMC long-term";
  if (raw.startsWith("SMC_")) return "SMC confirmation";
  return raw.replace(/_/g, " ");
}
function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(2);
}
function targetOf(item: ResearchDecisionCard): number | null {
  if (item.target_2 !== undefined && item.target_2 !== null) return item.target_2;
  if (item.target_1 !== undefined && item.target_1 !== null) return item.target_1;
  if (Array.isArray(item.targets) && item.targets.length > 0) return item.targets[item.targets.length - 1];
  return null;
}

// Entry-timing state (PR3) — READY/WATCH are actionable; IN_MOTION/MISSED are not.
const ENTRY_STATE_CHIP: Record<string, { label: string; color: string }> = {
  READY: { label: "Ready", color: "#34d399" },
  WATCH: { label: "Watch", color: "#7dd3fc" },
  IN_MOTION: { label: "In motion", color: "#fbbf24" },
  MISSED: { label: "Missed", color: "#fda4af" },
};

// Compact reachability status (mirrors backend bands) — a chip, not a sentence.
function statusChip(item: ResearchDecisionCard): { label: string; color: string } {
  const gap = item.entry_distance_pct;
  const g = gap != null ? gap.toFixed(0) : "?";
  switch (item.reachability) {
    case "actionable": return { label: "At entry", color: "#34d399" };
    case "waiting": return { label: `+${g}% · wait`, color: "#fbbf24" };
    case "unreachable": return { label: `+${g}% · ran past`, color: "#fda4af" };
    case "pre_breakout": return { label: "Below entry", color: "#7dd3fc" };
    default: return { label: "Cleared", color: "#34d399" };
  }
}

// Honest reward-left-from-here as a compact "R · %done" value. The headline R:R
// assumes a fill at the planned entry; once price has run toward target a buy at
// CMP carries far less reward for the same SL risk. Kept as a tidy column.
function remainingCompact(item: ResearchDecisionCard, target: number | null): { text: string; color: string } | null {
  const cmp = Number(item.scan_cmp);
  const entry = Number(item.entry_price);
  const sl = Number(item.stop_loss);
  if (!Number.isFinite(cmp) || !Number.isFinite(sl) || !Number.isFinite(entry) || !target) return null;
  if (cmp <= 0 || target <= entry) return null;
  const riskFromCmp = cmp - sl;
  if (riskFromCmp <= 0) return null;
  const remR = (target - cmp) / riskFromCmp;
  const pctDone = Math.round(((cmp - entry) / (target - entry)) * 100);
  const color = remR >= 1.5 ? "#34d399" : remR >= 0.5 ? "#fbbf24" : "#fda4af";
  return { text: `${remR.toFixed(2)}R · ${pctDone}%`, color };
}

export function FinalTrades({ items }: { items: ResearchDecisionCard[] }) {
  // Reachability: hide ideas whose planned entry price has already left behind
  // (>15% away → "unreachable"), behind a toggle so the default view only shows
  // ideas you can actually act on.
  const [showUnreachable, setShowUnreachable] = useState(false);
  const isUnreachable = useCallback((it: ResearchDecisionCard) => it.reachability === "unreachable", []);

  // Dynamic inventory: collapse past the limit with an expander.
  const COLLAPSED_LIMIT = 12;
  const [expanded, setExpanded] = useState(false);
  const filtered = useMemo(
    () => items.filter((it) => (showUnreachable ? true : !isUnreachable(it))),
    [items, showUnreachable, isUnreachable],
  );
  const visible = expanded ? filtered : filtered.slice(0, COLLAPSED_LIMIT);
  const unreachableCount = items.filter(isUnreachable).length;
  const collapsedCount = filtered.length - visible.length;

  return (
    <section className="glass border-emerald-500" style={{ padding: 18, display: "grid", gap: 14, border: "1px solid #10b981", boxShadow: "0 18px 44px rgba(16,185,129,0.12)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <Flame size={20} className="text-emerald-400 shrink-0" aria-hidden />
            <span>On the Radar</span>
          </h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>
            High-conviction setups being tracked for a clean entry — they move into the Portfolio only when price triggers, not buy-now calls
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {unreachableCount > 0 && (
            <button
              type="button"
              onClick={() => setShowUnreachable((s) => !s)}
              style={{ fontSize: "0.7rem", padding: "4px 9px", borderRadius: 6, background: showUnreachable ? "rgba(244,63,94,0.14)" : "rgba(15,23,42,0.6)", border: "1px solid rgba(244,63,94,0.32)", color: showUnreachable ? "#fda4af" : "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer" }}
              title="Ideas where price has run more than 15% past the planned entry — the limit order likely won't fill"
            >
              {showUnreachable ? <EyeOff size={12} /> : <Eye size={12} />}
              {showUnreachable ? "Hide ran-past-entry" : `Ran past entry (${unreachableCount})`}
            </button>
          )}
          <span style={{ fontSize: "0.72rem", padding: "4px 10px", borderRadius: 6, background: "rgba(16,185,129,0.14)", border: "1px solid rgba(16,185,129,0.5)", color: "#34d399", fontWeight: 900 }}>
            <span className="inline-flex items-center gap-1">
              <Flame size={12} aria-hidden />
              Actionable · {visible.length}
            </span>
          </span>
        </div>
      </div>

      {visible.length === 0 ? (
        <MarketMonitoringEmpty
          title={unreachableCount > 0 ? "Every actionable idea is at/near its entry" : "Radar is intentionally selective"}
          subtitle={unreachableCount > 0
            ? `${unreachableCount} idea(s) hidden because price already ran past the planned entry. Click "Ran past entry" above to review them.`
            : "Nothing cleared the last quality gate yet — the engine is still monitoring structure and liquidity. Use Watchlist and Discovery for names approaching confirmation."}
        />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table" style={{ minWidth: 860, width: "100%" }}>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Setup</th>
                <th style={{ textAlign: "right" }}>Conv.</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Entry</th>
                <th style={{ textAlign: "right" }}>SL</th>
                <th style={{ textAlign: "right" }}>Target</th>
                <th style={{ textAlign: "right" }}>From CMP</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((item) => {
                const symbol = cleanSymbol(item.symbol);
                const target = targetOf(item);
                const st = statusChip(item);
                const rc = remainingCompact(item, target);
                return (
                  <tr key={item.symbol}>
                    <td>
                      <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}>
                        {symbol} <ExternalLink size={11} style={{ opacity: 0.6 }} />
                      </Link>
                    </td>
                    <td style={{ color: "var(--text-secondary)", fontSize: "0.78rem" }}>{setupLabel(item.setup)}</td>
                    <td style={{ textAlign: "right", color: "#00e096", fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
                      {Number(item.confidence_score || 0).toFixed(1)}%
                    </td>
                    <td>
                      <div style={{ display: "inline-flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontSize: "0.68rem", padding: "2px 8px", borderRadius: 5, color: st.color, background: `color-mix(in srgb, ${st.color} 14%, transparent)`, border: `1px solid ${st.color}55`, fontWeight: 700, whiteSpace: "nowrap" }}>
                          {st.label}
                        </span>
                        {item.entry_state && ENTRY_STATE_CHIP[item.entry_state] && (
                          <span
                            title={item.entry_actionable ? "Actionable now" : "Not actionable — already moved"}
                            style={{ fontSize: "0.62rem", padding: "1px 6px", borderRadius: 5, color: ENTRY_STATE_CHIP[item.entry_state].color, background: `color-mix(in srgb, ${ENTRY_STATE_CHIP[item.entry_state].color} 12%, transparent)`, border: `1px solid ${ENTRY_STATE_CHIP[item.entry_state].color}55`, fontWeight: 700, whiteSpace: "nowrap" }}
                          >
                            {ENTRY_STATE_CHIP[item.entry_state].label}
                          </span>
                        )}
                      </div>
                    </td>
                    <td style={{ textAlign: "right", fontFamily: "monospace", fontSize: "0.8rem" }}>{fmt(item.entry_price)}</td>
                    <td style={{ textAlign: "right", fontFamily: "monospace", fontSize: "0.8rem", color: "#ff4e6a" }}>{fmt(item.stop_loss)}</td>
                    <td style={{ textAlign: "right", fontFamily: "monospace", fontSize: "0.8rem", color: "#00e096" }}>{fmt(target)}</td>
                    <td style={{ textAlign: "right", fontFamily: "monospace", fontSize: "0.76rem", color: rc ? rc.color : "var(--text-dim)", whiteSpace: "nowrap" }}>
                      {rc ? rc.text : "—"}
                    </td>
                    <td>
                      <div style={{ display: "inline-flex", alignItems: "center", gap: 6, justifyContent: "flex-end", width: "100%" }}>
                        <Link
                          href={`/research/chart?symbol=${encodeURIComponent(symbol)}&horizon=SWING`}
                          title="Study chart"
                          style={{ color: "#34d399", border: "1px solid rgba(16,185,129,0.4)", borderRadius: 6, padding: "4px 8px", textDecoration: "none", fontSize: "0.7rem", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 4, whiteSpace: "nowrap" }}
                        >
                          <LineChart size={12} /> Chart
                        </Link>
                        <AddToWatchlistButton
                          symbol={symbol}
                          compact
                          setup={{
                            entry_price: item.entry_price,
                            stop_loss: item.stop_loss,
                            target_1: item.target_1 ?? (Array.isArray(item.targets) ? item.targets[0] : null),
                            target_2: target,
                            pattern: setupLabel(item.setup),
                          }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(collapsedCount > 0 || expanded) && filtered.length > COLLAPSED_LIMIT && (
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 4 }}>
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            style={{ fontSize: "0.75rem", padding: "8px 16px", borderRadius: 8, background: "rgba(16,185,129,0.10)", border: "1px solid rgba(16,185,129,0.40)", color: "#34d399", cursor: "pointer", fontWeight: 800 }}
          >
            {expanded
              ? `Collapse to top ${COLLAPSED_LIMIT}`
              : `View all ${filtered.length} cleared setups (${collapsedCount} more)`}
          </button>
        </div>
      )}
    </section>
  );
}
