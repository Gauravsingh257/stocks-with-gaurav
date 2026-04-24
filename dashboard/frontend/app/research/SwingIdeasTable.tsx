"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import type { SwingIdea } from "@/lib/api";
import { api } from "@/lib/api";
import { CmpFreshnessBadge } from "./CmpFreshnessBadge";
import { SmcEvidencePanel } from "./SmcEvidencePanel";

const _sparkCache: Record<string, number[]> = {};

function Sparkline({ symbol, entry, sl }: { symbol: string; entry: number; sl: number }) {
  const [points, setPoints] = useState<number[] | null>(_sparkCache[symbol] || null);

  useEffect(() => {
    if (_sparkCache[symbol]) { setPoints(_sparkCache[symbol]); return; }
    let cancelled = false;
    api.researchChartData(symbol, "SWING").then((d) => {
      if (cancelled) return;
      const closes = (d?.candles ?? []).slice(-30).map((c: { close: number }) => c.close);
      _sparkCache[symbol] = closes;
      setPoints(closes);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [symbol]);

  if (!points || points.length < 5) return <div style={{ width: 80, height: 28 }} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const w = 80;
  const h = 28;
  const path = points.map((p, i) => {
    const x = (i / (points.length - 1)) * w;
    const y = h - ((p - min) / range) * (h - 4) - 2;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = points[points.length - 1];
  const color = last >= entry ? "#00d18c" : last <= sl ? "#ff4e6a" : "#5b9cf6";
  const entryY = h - ((entry - min) / range) * (h - 4) - 2;

  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <line x1={0} y1={entryY} x2={w} y2={entryY} stroke="rgba(255,255,255,0.12)" strokeWidth={0.5} strokeDasharray="2,2" />
      <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={w} cy={Number(path.split(",").pop())} r={2} fill={color} />
    </svg>
  );
}

interface Props {
  items: SwingIdea[];
  slotInfo?: string;
  onScan?: () => void;
  scanning?: boolean;
}

function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "-";
  return v.toFixed(2);
}

function _toIsoUtc(iso: string): string {
  // Normalize to a Date-parseable UTC string (handles "YYYY-MM-DD HH:MM:SS" and missing Z)
  const s = iso.replace(" ", "T");
  return s.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(s) ? s : s + "Z";
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    const d = new Date(_toIsoUtc(iso));
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  } catch {
    return "-";
  }
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    const d = new Date(_toIsoUtc(iso));
    return d.toLocaleString("en-IN", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: true,
    });
  } catch {
    return "-";
  }
}

function signalList(signals: Record<string, string>) {
  return Object.values(signals || {}).filter(Boolean);
}

function ReasoningModal({ item, onClose }: { item: SwingIdea; onClose: () => void }) {
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") onClose();
  }, [onClose]);

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [handleKeyDown]);

  const techSignals = signalList(item.technical_signals);
  const fundSignals = signalList(item.fundamental_signals);
  const sentSignals = signalList(item.sentiment_signals);

  return createPortal(
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.2 }}
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 9999,
        background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.92, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.25, ease: "easeOut" }}
        onClick={e => e.stopPropagation()}
        style={{
          background: "#111827", border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 12, maxWidth: 580, width: "100%", maxHeight: "80vh",
          overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
        }}
      >
        {/* Header */}
        <div style={{
          padding: "16px 20px", borderBottom: "1px solid rgba(255,255,255,0.08)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          position: "sticky", top: 0, background: "#111827", zIndex: 1,
        }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: "1rem" }}>{item.symbol}</div>
            <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginTop: 2 }}>Reasoning Evidence</div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 6, width: 32, height: 32, cursor: "pointer",
              color: "var(--text-secondary)", fontSize: "1.1rem",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: "16px 20px", display: "grid", gap: 16 }}>
          {/* Summary */}
          <div style={{
            fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.5,
            padding: "10px 14px", background: "rgba(255,255,255,0.03)", borderRadius: 8,
          }}>
            {item.reasoning_summary}
          </div>

          {/* Phase 2: Real SMC Evidence — replaces (est.) tech prose */}
          <SmcEvidencePanel evidence={item.smc_evidence} />

          {/* Technical */}
          {techSignals.length > 0 && (
            <div>
              <div style={{
                fontSize: "0.68rem", fontWeight: 700, color: "#5b9cf6",
                textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8,
              }}>
                Technical Factors
              </div>
              <div style={{ display: "grid", gap: 4 }}>
                {techSignals.map((s, i) => (
                  <div key={`t-${i}`} style={{
                    fontSize: "0.78rem", color: "var(--text-secondary)",
                    padding: "6px 10px", background: "rgba(91,156,246,0.05)",
                    borderRadius: 6, borderLeft: "2px solid rgba(91,156,246,0.3)",
                  }}>
                    {s}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Fundamental */}
          {fundSignals.length > 0 && (
            <div>
              <div style={{
                fontSize: "0.68rem", fontWeight: 700, color: "#00d18c",
                textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8,
              }}>
                Fundamental Factors
              </div>
              <div style={{ display: "grid", gap: 4 }}>
                {fundSignals.map((s, i) => (
                  <div key={`f-${i}`} style={{
                    fontSize: "0.78rem", color: "var(--text-secondary)",
                    padding: "6px 10px", background: "rgba(0,209,140,0.05)",
                    borderRadius: 6, borderLeft: "2px solid rgba(0,209,140,0.3)",
                  }}>
                    {s}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Sentiment */}
          {sentSignals.length > 0 && (
            <div>
              <div style={{
                fontSize: "0.68rem", fontWeight: 700, color: "#f0c060",
                textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8,
              }}>
                Sentiment Factors
              </div>
              <div style={{ display: "grid", gap: 4 }}>
                {sentSignals.map((s, i) => (
                  <div key={`s-${i}`} style={{
                    fontSize: "0.78rem", color: "var(--text-secondary)",
                    padding: "6px 10px", background: "rgba(240,192,96,0.05)",
                    borderRadius: 6, borderLeft: "2px solid rgba(240,192,96,0.3)",
                  }}>
                    {s}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>,
    document.body
  );
}

function chartUrl(symbol: string, horizon = "SWING") {
  const s = symbol.replace("NSE:", "");
  return `/research/chart?symbol=${encodeURIComponent(s)}&horizon=${horizon}`;
}

interface LevelsTooltipProps {
  item: SwingIdea;
}

function LevelsTooltip({ item }: LevelsTooltipProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <a
        href={chartUrl(item.symbol)}
        title="Open Chart with Levels"
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: "4px 10px",
          borderRadius: 6,
          background: "rgba(41, 98, 255, 0.18)",
          border: "1px solid rgba(41, 98, 255, 0.45)",
          color: "#5b9cf6",
          fontSize: "0.75rem",
          fontWeight: 600,
          textDecoration: "none",
          cursor: "pointer",
          transition: "background 0.15s",
          whiteSpace: "nowrap",
        }}
      >
        <svg width="13" height="13" viewBox="0 0 13 13" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="2" y="4" width="3" height="6" rx="0.5" fill="#5b9cf6"/>
          <line x1="3.5" y1="1" x2="3.5" y2="4" stroke="#5b9cf6" strokeWidth="1.2"/>
          <line x1="3.5" y1="10" x2="3.5" y2="12" stroke="#5b9cf6" strokeWidth="1.2"/>
          <rect x="8" y="3" width="3" height="5" rx="0.5" fill="#00d18c"/>
          <line x1="9.5" y1="1" x2="9.5" y2="3" stroke="#00d18c" strokeWidth="1.2"/>
          <line x1="9.5" y1="8" x2="9.5" y2="11" stroke="#00d18c" strokeWidth="1.2"/>
        </svg>
        Chart
      </a>

      {visible && (
        <div style={{
          position: "absolute",
          top: "calc(100% + 6px)",
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 50,
          background: "#1a2035",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "10px 14px",
          minWidth: 200,
          boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
          pointerEvents: "none",
        }}>
          <div style={{ fontSize: "0.7rem", fontWeight: 700, color: "#5b9cf6", marginBottom: 8, letterSpacing: "0.06em" }}>
            KEY LEVELS
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: "0.75rem" }}>
            <span style={{ color: "var(--text-secondary)" }}>Entry</span>
            <span style={{ color: "#ffffff", fontWeight: 600 }}>{fmt(item.entry_price)}</span>
            <span style={{ color: "var(--text-secondary)" }}>Stop Loss</span>
            <span style={{ color: "#ff4e6a", fontWeight: 600 }}>{fmt(item.stop_loss)}</span>
            <span style={{ color: "var(--text-secondary)" }}>Target 1</span>
            <span style={{ color: "#00d18c", fontWeight: 600 }}>{fmt(item.target_1)}</span>
            {item.target_2 != null && (
              <>
                <span style={{ color: "var(--text-secondary)" }}>Target 2</span>
                <span style={{ color: "#00d18c", fontWeight: 600 }}>{fmt(item.target_2)}</span>
              </>
            )}
            <span style={{ color: "var(--text-secondary)" }}>R:R</span>
            <span style={{ color: "#f0c060", fontWeight: 600 }}>{item.risk_reward?.toFixed(2) ?? "-"}</span>
          </div>
          <div style={{
            position: "absolute",
            top: -5,
            left: "50%",
            transform: "translateX(-50%)",
            width: 10,
            height: 10,
            background: "#1a2035",
            border: "1px solid var(--border)",
            borderRight: "none",
            borderBottom: "none",
            rotate: "45deg",
          }} />
        </div>
      )}
    </div>
  );
}

function DataBadge({ auth }: { auth?: string }) {
  if (!auth || auth === "unknown") return null;
  const colors: Record<string, { bg: string; fg: string; label: string }> = {
    real: { bg: "rgba(0,209,140,0.15)", fg: "#00d18c", label: "Verified Data" },
    partial: { bg: "rgba(240,192,96,0.15)", fg: "#f0c060", label: "Partial Data" },
    synthetic: { bg: "rgba(255,78,106,0.15)", fg: "#ff4e6a", label: "Estimated" },
  };
  const c = colors[auth] || colors.partial!;
  return (
    <span style={{
      fontSize: "0.65rem", padding: "2px 6px", borderRadius: 4,
      background: c.bg, color: c.fg, fontWeight: 600, whiteSpace: "nowrap",
    }}>
      {c.label}
    </span>
  );
}

function StatusBadge({ status }: { status?: string }) {
  if (!status || status === "ACTIVE") return null;
  const colors: Record<string, { bg: string; fg: string }> = {
    ARCHIVED: { bg: "rgba(148,163,184,0.15)", fg: "#94a3b8" },
    EXPIRED: { bg: "rgba(255,78,106,0.15)", fg: "#ff4e6a" },
  };
  const c = colors[status] || colors.ARCHIVED!;
  return (
    <span style={{
      fontSize: "0.6rem", padding: "1px 5px", borderRadius: 3,
      background: c.bg, color: c.fg, fontWeight: 600, whiteSpace: "nowrap", marginLeft: 4,
    }}>
      {status}
    </span>
  );
}

function EntryGapBadge({ gap }: { gap?: number | null }) {
  if (gap === null || gap === undefined) return <span style={{ color: "var(--text-dim)", fontSize: "0.7rem" }}>-</span>;
  const absGap = Math.abs(gap);
  let color = "#00d18c"; // green < 2%
  if (absGap > 5) color = "#ff4e6a"; // red > 5%
  else if (absGap > 2) color = "#f0c060"; // yellow 2-5%
  return (
    <span style={{ fontSize: "0.72rem", fontWeight: 600, color }}>
      {gap >= 0 ? "+" : ""}{gap.toFixed(1)}%
    </span>
  );
}

function ActionTag({ tag }: { tag?: string }) {
  if (!tag) return null;
  const configs: Record<string, { bg: string; fg: string; label: string; icon: string }> = {
    EXECUTE_NOW: { bg: "rgba(0,209,140,0.18)", fg: "#00d18c", label: "Execute Now", icon: "\u{1F7E2}" },
    WAIT_FOR_RETEST: { bg: "rgba(240,192,96,0.18)", fg: "#f0c060", label: "Wait for Retest", icon: "\u{1F7E1}" },
    IN_MOTION: { bg: "rgba(120,160,255,0.18)", fg: "#7ea8ff", label: "In Progress", icon: "\u{1F535}" },
    MISSED: { bg: "rgba(255,78,106,0.18)", fg: "#ff4e6a", label: "Missed Trade", icon: "\u{1F534}" },
  };
  const c = configs[tag] || configs.WAIT_FOR_RETEST!;
  return (
    <span style={{
      fontSize: "0.65rem", padding: "2px 7px", borderRadius: 4, fontWeight: 600,
      background: c.bg, color: c.fg, whiteSpace: "nowrap",
      display: "inline-flex", alignItems: "center", gap: 3,
    }}>
      <span style={{ fontSize: "0.6rem" }}>{c.icon}</span> {c.label}
    </span>
  );
}

function QualityRing({ score }: { score: number }) {
  const pct = Math.min(score, 100);
  const r = 13;
  const circ = 2 * Math.PI * r;
  const offset = circ - (pct / 100) * circ;
  const color = pct >= 70 ? "#00d18c" : pct >= 50 ? "#f0c060" : "#ff4e6a";
  return (
    <div style={{ position: "relative", width: 32, height: 32, flexShrink: 0 }}>
      <svg width={32} height={32} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={16} cy={16} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={3} />
        <circle cx={16} cy={16} r={r} fill="none" stroke={color} strokeWidth={3}
          strokeDasharray={circ} strokeDashoffset={offset} strokeLinecap="round" />
      </svg>
      <span style={{
        position: "absolute", inset: 0, display: "flex", alignItems: "center",
        justifyContent: "center", fontSize: "0.55rem", fontWeight: 700, color,
      }}>
        {Math.round(pct)}
      </span>
    </div>
  );
}

function RRBar({ rr }: { rr: number }) {
  const capped = Math.min(rr, 5);
  const pct = (capped / 5) * 100;
  const color = rr >= 3 ? "#00d18c" : rr >= 2 ? "#5b9cf6" : rr >= 1.5 ? "#f0c060" : "#ff4e6a";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 60 }}>
      <div style={{ flex: 1, height: 5, borderRadius: 3, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", borderRadius: 3, background: color }} />
      </div>
      <span style={{ fontSize: "0.68rem", fontWeight: 700, color, whiteSpace: "nowrap" }}>1:{rr.toFixed(1)}</span>
    </div>
  );
}

function FundBadge({ value, suffix, good, warn }: { value?: number | null; suffix?: string; good: number; warn: number }) {
  if (value === null || value === undefined) return <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>-</span>;
  const color = value >= good ? "#00d18c" : value >= warn ? "#f0c060" : "#ff4e6a";
  return <span style={{ fontSize: "0.72rem", fontWeight: 600, color }}>{value.toFixed(1)}{suffix ?? ""}</span>;
}

type SortKey = "confidence_score" | "entry_gap_pct" | "risk_reward" | "pe_ratio" | "roe_pct" | "market_cap_cr" | null;

function useSortedItems(items: SwingIdea[], sortKey: SortKey, sortAsc: boolean): SwingIdea[] {
  if (!sortKey) return items;
  return [...items].sort((a, b) => {
    const av = (a as unknown as Record<string, unknown>)[sortKey] as number | null | undefined;
    const bv = (b as unknown as Record<string, unknown>)[sortKey] as number | null | undefined;
    const na = av ?? (sortAsc ? Infinity : -Infinity);
    const nb = bv ?? (sortAsc ? Infinity : -Infinity);
    return sortAsc ? na - nb : nb - na;
  });
}

export function SwingIdeasTable({ items, slotInfo, onScan, scanning }: Props) {
  const [reasoningItem, setReasoningItem] = useState<SwingIdea | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>(null);
  const [sortAsc, setSortAsc] = useState(false);
  const [filterAction, setFilterAction] = useState<string | null>(null);

  const filtered = filterAction ? items.filter(i => i.action_tag === filterAction) : items;
  const sorted = useSortedItems(filtered, sortKey, sortAsc);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const sortableStyle = (key: SortKey): React.CSSProperties => ({
    textAlign: "left", padding: "8px 12px", fontWeight: 500, whiteSpace: "nowrap",
    cursor: "pointer", userSelect: "none",
    color: sortKey === key ? "var(--accent)" : "var(--text-secondary)",
  });

  const sortArrow = (key: SortKey) => sortKey === key ? (sortAsc ? " ↑" : " ↓") : "";

  const actionTags = Array.from(new Set(items.map(i => i.action_tag).filter(Boolean)));

  const headers = ["#", "Symbol", "Entry", "CMP", "Gap", "Action", "SL", "T1", "R:R", "Conf.", "PE", "MCap", "Chart", "Reasoning"];

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontWeight: 600 }}>Swing Trade Opportunities</span>
          {slotInfo && <span style={{ fontSize: "0.75rem", color: "var(--accent)", fontWeight: 500 }}>{slotInfo}</span>}
        </div>
        {items.length > 0 && actionTags.length > 1 && (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            <button
              onClick={() => setFilterAction(null)}
              style={{
                padding: "2px 8px", borderRadius: 4, fontSize: "0.65rem", fontWeight: 600,
                border: "1px solid", cursor: "pointer",
                background: !filterAction ? "rgba(0,212,255,0.15)" : "transparent",
                borderColor: !filterAction ? "rgba(0,212,255,0.4)" : "rgba(255,255,255,0.08)",
                color: !filterAction ? "var(--accent)" : "var(--text-dim)",
              }}
            >
              All
            </button>
            {actionTags.map(tag => (
              <button
                key={tag}
                onClick={() => setFilterAction(filterAction === tag ? null : tag!)}
                style={{
                  padding: "2px 8px", borderRadius: 4, fontSize: "0.65rem", fontWeight: 600,
                  border: "1px solid", cursor: "pointer",
                  background: filterAction === tag ? "rgba(0,212,255,0.15)" : "transparent",
                  borderColor: filterAction === tag ? "rgba(0,212,255,0.4)" : "rgba(255,255,255,0.08)",
                  color: filterAction === tag ? "var(--accent)" : "var(--text-dim)",
                }}
              >
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>
      {items.length === 0 ? (
        <div style={{ padding: "24px", textAlign: "center" }}>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.9rem", fontWeight: 500 }}>
            No high-quality swing opportunities found
          </div>
          <div style={{ color: "var(--text-dim)", fontSize: "0.78rem", marginTop: 6 }}>
            The system only recommends stocks when genuine SMC setups are detected.
          </div>
          {onScan && (
            <button
              onClick={onScan}
              disabled={scanning}
              style={{
                marginTop: 12, padding: "6px 16px", borderRadius: 8, fontWeight: 600,
                fontSize: "0.75rem", cursor: scanning ? "wait" : "pointer",
                background: "rgba(0,212,255,0.12)", border: "1px solid rgba(0,212,255,0.3)",
                color: "var(--accent)", opacity: scanning ? 0.6 : 1,
              }}
            >
              {scanning ? "Scanning..." : "Run Swing Scan"}
            </button>
          )}
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block" style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  {headers.map(h => {
                    const keyMap: Record<string, SortKey> = { "Conf.": "confidence_score", "PE": "pe_ratio", "MCap": "market_cap_cr", "Gap": "entry_gap_pct" };
                    const sk = keyMap[h];
                    if (sk) return <th key={h} onClick={() => toggleSort(sk)} style={sortableStyle(sk)}>{h}{sortArrow(sk)}</th>;
                    return <th key={h} style={{ textAlign: "left", padding: "8px 12px", fontWeight: 500, whiteSpace: "nowrap" }}>{h}</th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {sorted.map((item, idx) => (
                  <tr key={item.id} style={{ borderBottom: "1px solid var(--border-muted)" }}>
                    <td style={{ padding: "10px 12px", color: "var(--text-secondary)", fontSize: "0.75rem", fontWeight: 500 }}>{idx + 1}</td>
                    <td style={{ padding: "10px 12px", fontWeight: 600 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <QualityRing score={item.confidence_score} />
                        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                          <a href={`https://www.tradingview.com/chart/?symbol=${item.symbol.replace("NSE:", "NSE%3A")}`}
                            target="_blank" rel="noopener noreferrer"
                            style={{ color: "inherit", textDecoration: "none" }} title="Open on TradingView">
                            {item.symbol.replace("NSE:", "")}<span style={{ fontSize: "0.55rem", marginLeft: 3, opacity: 0.4 }}>↗</span>
                          </a>
                          {item.sector && <span style={{ fontSize: "0.58rem", color: "var(--text-secondary)", fontWeight: 500, letterSpacing: 0.3, textTransform: "uppercase" }}>{item.sector}</span>}
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: "10px 12px" }}>{fmt(item.entry_price)}</td>
                    <td style={{ padding: "10px 12px", fontWeight: 500 }}>
                      {item.scan_cmp ? fmt(item.scan_cmp) : "-"}
                      <CmpFreshnessBadge source={item.cmp_source} ageSec={item.cmp_age_sec} />
                    </td>
                    <td style={{ padding: "10px 12px" }}><EntryGapBadge gap={item.entry_gap_pct} /></td>
                    <td style={{ padding: "10px 12px" }}><ActionTag tag={item.action_tag} /></td>
                    <td style={{ padding: "10px 12px", color: "#ff4e6a", fontSize: "0.78rem" }}>{fmt(item.stop_loss)}</td>
                    <td style={{ padding: "10px 12px", color: "#00d18c", fontSize: "0.78rem" }}>{fmt(item.target_1)}</td>
                    <td style={{ padding: "8px 10px" }}><RRBar rr={item.risk_reward} /></td>
                    <td style={{ padding: "10px 12px", color: "#00ff88", fontSize: "0.78rem", fontWeight: 600 }}>{item.confidence_score.toFixed(0)}%</td>
                    <td style={{ padding: "10px 12px" }}><FundBadge value={item.pe_ratio} good={0} warn={30} /></td>
                    <td style={{ padding: "10px 12px" }}>
                      {item.market_cap_cr != null ? (
                        <span style={{ fontSize: "0.72rem", fontWeight: 500, color: "var(--text-secondary)" }}>
                          {item.market_cap_cr >= 10000 ? `${(item.market_cap_cr / 10000).toFixed(1)}L Cr` : `${Math.round(item.market_cap_cr)} Cr`}
                        </span>
                      ) : <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>-</span>}
                    </td>
                    <td style={{ padding: "6px 8px" }}><Sparkline symbol={item.symbol} entry={item.entry_price} sl={item.stop_loss} /></td>
                    <td style={{ padding: "10px 12px" }}>
                      <button onClick={() => setReasoningItem(item)}
                        style={{ background: "rgba(41,98,255,0.12)", border: "1px solid rgba(41,98,255,0.3)", borderRadius: 6, padding: "5px 10px", cursor: "pointer", color: "var(--accent)", fontSize: "0.72rem", fontWeight: 600, whiteSpace: "nowrap" }}>
                        View Evidence
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden" style={{ display: "flex", flexDirection: "column", gap: 8, padding: "8px 12px" }}>
            {sorted.map((item) => (
              <div key={item.id} style={{ border: "1px solid var(--border-muted)", borderRadius: 10, padding: "12px 14px", background: "rgba(255,255,255,0.02)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <QualityRing score={item.confidence_score} />
                    <div>
                      <a href={`https://www.tradingview.com/chart/?symbol=${item.symbol.replace("NSE:", "NSE%3A")}`}
                        target="_blank" rel="noopener noreferrer"
                        style={{ color: "inherit", textDecoration: "none", fontWeight: 700, fontSize: "0.95rem" }}>
                        {item.symbol.replace("NSE:", "")}<span style={{ fontSize: "0.6rem", marginLeft: 3, opacity: 0.4 }}>↗</span>
                      </a>
                      {item.sector && <div style={{ fontSize: "0.6rem", color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.3 }}>{item.sector}</div>}
                    </div>
                  </div>
                  <ActionTag tag={item.action_tag} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px 12px", fontSize: "0.74rem", marginBottom: 8 }}>
                  <div><span style={{ color: "var(--text-dim)" }}>Entry</span><br /><strong>{fmt(item.entry_price)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>CMP</span><br /><strong>{item.scan_cmp ? fmt(item.scan_cmp) : "-"}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>Gap</span><br /><EntryGapBadge gap={item.entry_gap_pct} /></div>
                  <div><span style={{ color: "var(--text-dim)" }}>SL</span><br /><strong style={{ color: "#ff4e6a" }}>{fmt(item.stop_loss)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>Target</span><br /><strong style={{ color: "#00d18c" }}>{fmt(item.target_1)}</strong></div>
                  <div><span style={{ color: "var(--text-dim)" }}>PE</span><br /><FundBadge value={item.pe_ratio} good={0} warn={30} /></div>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <RRBar rr={item.risk_reward} />
                  <Sparkline symbol={item.symbol} entry={item.entry_price} sl={item.stop_loss} />
                  <button onClick={() => setReasoningItem(item)}
                    style={{ background: "rgba(41,98,255,0.12)", border: "1px solid rgba(41,98,255,0.3)", borderRadius: 6, padding: "4px 10px", cursor: "pointer", color: "var(--accent)", fontSize: "0.68rem", fontWeight: 600, whiteSpace: "nowrap", flexShrink: 0 }}>
                    Evidence
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <AnimatePresence>
        {reasoningItem && <ReasoningModal item={reasoningItem} onClose={() => setReasoningItem(null)} />}
      </AnimatePresence>
    </div>
  );
}
