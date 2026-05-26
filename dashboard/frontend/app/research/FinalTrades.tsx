"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Activity, Check, ExternalLink, Eye, EyeOff, Flame, ShieldCheck, Target, X,
} from "lucide-react";
import type { ResearchDecisionCard } from "@/lib/api";
import MarketMonitoringEmpty from "@/components/MarketMonitoringEmpty";
import AddToWatchlistButton from "@/components/AddToWatchlistButton";

const STORAGE_PREFIX = "research:dismissed:";

type DismissAction = "reviewed" | "skipped";

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
function dismissalKey(symbol: string): string {
  return `${STORAGE_PREFIX}${symbol}:${todayISO()}`;
}
function readDismissed(symbol: string): DismissAction | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(dismissalKey(symbol));
    return v === "reviewed" || v === "skipped" ? v : null;
  } catch {
    return null;
  }
}
function writeDismissed(symbol: string, action: DismissAction): void {
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(dismissalKey(symbol), action); } catch { /* quota / private mode */ }
}
function clearDismissed(symbol: string): void {
  if (typeof window === "undefined") return;
  try { window.localStorage.removeItem(dismissalKey(symbol)); } catch { /* quota / private mode */ }
}
function pruneStaleDismissals(): void {
  if (typeof window === "undefined") return;
  try {
    const today = todayISO();
    const toRemove: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (!k || !k.startsWith(STORAGE_PREFIX)) continue;
      const day = k.split(":").pop();
      if (day && day !== today) toRemove.push(k);
    }
    toRemove.forEach((k) => window.localStorage.removeItem(k));
  } catch { /* ignore */ }
}

function cleanSymbol(symbol: string): string {
  return symbol.replace(/^NSE:/i, "").replace(/\.NS$/i, "");
}
function setupLabel(setup: string | null | undefined): string {
  const raw = String(setup || "").trim();
  if (!raw) return "SMC confirmed";
  if (raw === "MOMENTUM_FALLBACK") return "Momentum breakout candidate";
  if (raw.startsWith("SMC_SWING")) return "SMC swing confirmation";
  if (raw.startsWith("SMC_LONGTERM")) return "SMC long-term confirmation";
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

function shortReason(item: ResearchDecisionCard): string {
  const signals = item.technical_signals || {};
  const evidence = [signals.daily_structure, signals.structure, signals.ob_fvg, signals.ob_liquidity]
    .filter(Boolean)
    .slice(0, 2)
    .join(" + ");
  if (evidence) return `Reason: ${evidence}`;
  if (item.reasoning) return `Reason: ${item.reasoning.split(".")[0]}`;
  return `Reason: ${setupLabel(item.setup) || "SMC confirmation and risk levels are aligned"}`;
}

function confidenceText(item: ResearchDecisionCard): string {
  const score = Number(item.confidence_score || 0);
  if (score >= 70) return "Confidence: Strong because SMC, quality, and execution layers are aligned.";
  if (score >= 60) return "Confidence: Good because the setup passed final SMC scoring with defined levels.";
  return "Confidence: Review-ready, but confirm the chart before any manual action.";
}

function riskNote(item: ResearchDecisionCard, target: number | null): string {
  const rr = item.risk_reward ? `R:R ${fmt(item.risk_reward)}` : target ? "target mapped" : "target pending";
  return `Risk note: Use SL ${fmt(item.stop_loss)}. ${rr}. Avoid entry if price is far from the planned zone.`;
}

export function FinalTrades({ items }: { items: ResearchDecisionCard[] }) {
  const [dismissedMap, setDismissedMap] = useState<Record<string, DismissAction>>({});
  const [showDismissed, setShowDismissed] = useState(false);

  useEffect(() => {
    pruneStaleDismissals();
    const next: Record<string, DismissAction> = {};
    for (const it of items) {
      const v = readDismissed(it.symbol);
      if (v) next[it.symbol] = v;
    }
    setDismissedMap(next);
  }, [items]);

  const dismiss = useCallback((symbol: string, action: DismissAction) => {
    writeDismissed(symbol, action);
    setDismissedMap((prev) => ({ ...prev, [symbol]: action }));
  }, []);

  const restore = useCallback((symbol: string) => {
    clearDismissed(symbol);
    setDismissedMap((prev) => {
      const next = { ...prev };
      delete next[symbol];
      return next;
    });
  }, []);

  // PR A keeps the legacy 6-card cap; PR B removes it and adds an expander.
  const candidates = items.slice(0, 6);
  const visible = useMemo(
    () => (showDismissed ? candidates : candidates.filter((it) => !dismissedMap[it.symbol])),
    [candidates, dismissedMap, showDismissed]
  );
  const hiddenCount = candidates.length - visible.length;

  return (
    <section className="glass border-emerald-500 shadow-xl" style={{ padding: 18, display: "grid", gap: 14, border: "1px solid #10b981", boxShadow: "0 24px 60px rgba(16,185,129,0.16), 0 0 28px rgba(16,185,129,0.14)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 className="m-0 text-lg font-bold flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <Flame size={20} className="text-emerald-400 shrink-0" aria-hidden />
            <span>Final Trade Ideas</span>
          </h2>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: "0.78rem" }}>
            Engine has done all the homework — open the chart and decide
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {(hiddenCount > 0 || showDismissed) && (
            <button
              type="button"
              onClick={() => setShowDismissed((s) => !s)}
              style={{ fontSize: "0.7rem", padding: "4px 9px", borderRadius: 6, background: "rgba(15,23,42,0.6)", border: "1px solid rgba(148,163,184,0.32)", color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer" }}
              title={showDismissed ? "Hide dismissed cards" : "Show dismissed cards for today"}
            >
              {showDismissed ? <EyeOff size={12} /> : <Eye size={12} />}
              {showDismissed ? "Hide dismissed" : `Show dismissed (${hiddenCount})`}
            </button>
          )}
          <span style={{ fontSize: "0.72rem", padding: "4px 10px", borderRadius: 6, background: "rgba(16,185,129,0.14)", border: "1px solid rgba(16,185,129,0.5)", color: "#34d399", fontWeight: 900, boxShadow: "0 0 18px rgba(16,185,129,0.18)" }}>
            <span className="inline-flex items-center gap-1">
              <Flame size={12} aria-hidden />
              Cleared · {visible.length}
            </span>
          </span>
        </div>
      </div>

      {visible.length === 0 ? (
        <MarketMonitoringEmpty
          title={hiddenCount > 0 ? "All cleared cards reviewed for today" : "Final book is intentionally selective"}
          subtitle={hiddenCount > 0
            ? `${hiddenCount} card(s) dismissed today. They reset tomorrow, or click "Show dismissed" above.`
            : "Nothing cleared the last quality gate yet — the engine is still monitoring structure and liquidity. Use Watchlist and Discovery for names approaching confirmation."}
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 14, padding: "4px 2px" }}>
          {visible.map((item) => {
            const symbol = cleanSymbol(item.symbol);
            const target = targetOf(item);
            const action = dismissedMap[item.symbol];
            return (
              <article className="scale-105 border-emerald-500 shadow-xl" key={item.symbol} style={{ border: "1px solid #10b981", borderRadius: 8, padding: 14, background: "linear-gradient(180deg, rgba(16,185,129,0.12), rgba(16,185,129,0.045))", display: "grid", gap: 11, transform: "scale(1.03)", transformOrigin: "center", boxShadow: "0 18px 42px rgba(16,185,129,0.18), 0 0 24px rgba(16,185,129,0.16)", opacity: action ? 0.55 : 1 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}>
                  <div>
                    <Link href={`/stock/${encodeURIComponent(symbol)}`} style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 850, display: "inline-flex", alignItems: "center", gap: 5 }}>
                      {symbol} <ExternalLink size={12} />
                    </Link>
                    <div style={{ color: "var(--text-dim)", fontSize: "0.68rem", marginTop: 2 }}>{setupLabel(item.setup)}</div>
                  </div>
                  <div style={{ color: "#00e096", fontWeight: 850, fontSize: "0.86rem" }}>{Number(item.confidence_score || 0).toFixed(1)}%</div>
                </div>

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: "0.66rem", padding: "3px 7px", borderRadius: 6, color: "#00e096", background: "rgba(0,224,150,0.12)", border: "1px solid rgba(0,224,150,0.28)", fontWeight: 800, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <ShieldCheck size={12} /> High Conviction
                  </span>
                  <span style={{ fontSize: "0.68rem", padding: "3px 8px", borderRadius: 6, color: "#34d399", background: "rgba(16,185,129,0.16)", border: "1px solid rgba(16,185,129,0.4)", fontWeight: 900, display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <Activity size={12} /> Cleared — study chart
                  </span>
                  {action && (
                    <span style={{ fontSize: "0.66rem", padding: "3px 7px", borderRadius: 6, color: action === "reviewed" ? "#7dd3fc" : "#fda4af", background: action === "reviewed" ? "rgba(125,211,252,0.12)" : "rgba(253,164,175,0.10)", border: `1px solid ${action === "reviewed" ? "rgba(125,211,252,0.40)" : "rgba(253,164,175,0.35)"}`, fontWeight: 800 }}>
                      {action === "reviewed" ? "Reviewed today" : "Skipped today"}
                    </span>
                  )}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, fontSize: "0.74rem" }}>
                  <div><span style={{ color: "var(--text-dim)" }}>Entry</span><br /><strong>{fmt(item.entry_price)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>SL</span><br /><strong style={{ color: "#ff4e6a" }}>{fmt(item.stop_loss)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>Target</span><br /><strong style={{ color: "#00e096" }}>{fmt(target)}</strong></div>
                </div>

                <div style={{ display: "grid", gap: 6, padding: 10, borderRadius: 8, background: "rgba(3,7,18,0.28)", border: "1px solid rgba(16,185,129,0.22)", fontSize: "0.72rem", lineHeight: 1.45 }}>
                  <div style={{ color: "var(--text-primary)", fontWeight: 850 }}>Why this trade?</div>
                  <div style={{ color: "var(--text-secondary)" }}>{shortReason(item)}</div>
                  <div style={{ color: "#a7f3d0" }}>{confidenceText(item)}</div>
                  <div style={{ color: "#fca5a5" }}>{riskNote(item, target)}</div>
                </div>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                  <AddToWatchlistButton symbol={symbol} compact />
                  <Link href={`/research/chart?symbol=${encodeURIComponent(symbol)}&horizon=SWING`} style={{ color: "#04130d", background: "#34d399", border: "1px solid rgba(16,185,129,0.7)", borderRadius: 7, padding: "8px 10px", textDecoration: "none", fontSize: "0.74rem", fontWeight: 900, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
                    <Target size={13} /> Study Chart
                  </Link>
                  <div style={{ marginLeft: "auto", display: "inline-flex", gap: 6 }}>
                    {action ? (
                      <button
                        type="button"
                        onClick={() => restore(item.symbol)}
                        title="Undo dismissal"
                        style={{ fontSize: "0.7rem", padding: "6px 9px", borderRadius: 6, background: "rgba(15,23,42,0.55)", border: "1px solid rgba(148,163,184,0.35)", color: "var(--text-secondary)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
                      >
                        Undo
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => dismiss(item.symbol, "reviewed")}
                          title="Mark as reviewed — hides this card for the day"
                          style={{ fontSize: "0.7rem", padding: "6px 9px", borderRadius: 6, background: "rgba(125,211,252,0.10)", border: "1px solid rgba(125,211,252,0.35)", color: "#7dd3fc", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 700 }}
                        >
                          <Check size={12} /> Reviewed
                        </button>
                        <button
                          type="button"
                          onClick={() => dismiss(item.symbol, "skipped")}
                          title="Skip this card for the day"
                          style={{ fontSize: "0.7rem", padding: "6px 9px", borderRadius: 6, background: "rgba(253,164,175,0.08)", border: "1px solid rgba(253,164,175,0.32)", color: "#fda4af", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4, fontWeight: 700 }}
                        >
                          <X size={12} /> Skip
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
