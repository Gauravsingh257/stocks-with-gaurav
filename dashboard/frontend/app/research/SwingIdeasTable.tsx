"use client";

import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import type { SwingIdea } from "@/lib/api";
import { CmpFreshnessBadge } from "./CmpFreshnessBadge";
import { SmcEvidencePanel } from "./SmcEvidencePanel";

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

function FundBadge({ value, suffix, good, warn }: { value?: number | null; suffix?: string; good: number; warn: number }) {
  if (value === null || value === undefined) return <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>-</span>;
  const color = value >= good ? "#00d18c" : value >= warn ? "#f0c060" : "#ff4e6a";
  return <span style={{ fontSize: "0.72rem", fontWeight: 600, color }}>{value.toFixed(1)}{suffix ?? ""}</span>;
}

export function SwingIdeasTable({ items, slotInfo, onScan, scanning }: Props) {
  const [reasoningItem, setReasoningItem] = useState<SwingIdea | null>(null);
  const headers = ["#", "Symbol", "Entry", "CMP", "Gap", "Type", "Action", "Stop Loss", "T1", "T2", "Conf.", "PE", "ROE", "MCap", "Data", "Chart", "Detected", "Updated", "Reasoning"];

  return (
    <div className="glass" style={{ overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--border)", fontWeight: 600, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Swing Trade Opportunities</span>
        {slotInfo && <span style={{ fontSize: "0.75rem", color: "var(--accent)", fontWeight: 500 }}>{slotInfo}</span>}
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
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                {headers.map(h => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 12px", fontWeight: 500, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => (
                <tr key={item.id} style={{ borderBottom: "1px solid var(--border-muted)" }}>
                  <td style={{ padding: "10px 12px", color: "var(--text-secondary)", fontSize: "0.75rem", fontWeight: 500 }}>{idx + 1}</td>
                  <td style={{ padding: "10px 12px", fontWeight: 600 }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span>{item.symbol}</span>
                      {item.sector ? (
                        <span style={{
                          fontSize: "0.6rem", color: "var(--text-secondary)",
                          fontWeight: 500, letterSpacing: 0.3, textTransform: "uppercase",
                        }}>
                          {item.sector}
                        </span>
                      ) : null}
                    </div>
                  </td>
                  <td style={{ padding: "10px 12px" }}>{fmt(item.entry_price)}</td>
                  <td style={{ padding: "10px 12px", fontWeight: 500 }}>
                    {item.scan_cmp ? fmt(item.scan_cmp) : "-"}
                    <CmpFreshnessBadge source={item.cmp_source} ageSec={item.cmp_age_sec} />
                  </td>
                  <td style={{ padding: "10px 12px" }}><EntryGapBadge gap={item.entry_gap_pct} /></td>
                  <td style={{ padding: "10px 12px" }}>
                    <span style={{
                      fontSize: "0.65rem", padding: "2px 6px", borderRadius: 4, fontWeight: 600,
                      whiteSpace: "nowrap",
                      background: item.entry_type === "LIMIT" ? "rgba(41, 98, 255, 0.15)" : "rgba(0, 209, 140, 0.15)",
                      color: item.entry_type === "LIMIT" ? "#5b9cf6" : "#00d18c",
                    }}>
                      {item.entry_type === "LIMIT" ? "LIMIT (Zone)" : "MARKET"}
                    </span>
                  </td>
                  <td style={{ padding: "10px 12px" }}><ActionTag tag={item.action_tag} /></td>
                  <td style={{ padding: "10px 12px", color: "#ff4e6a" }}>{fmt(item.stop_loss)}</td>
                  <td style={{ padding: "10px 12px", color: "#00d18c" }}>{fmt(item.target_1)}</td>
                  <td style={{ padding: "10px 12px", color: "#00d18c" }}>{fmt(item.target_2)}</td>
                  <td style={{ padding: "10px 12px", color: "#00ff88" }}>{item.confidence_score.toFixed(1)}%</td>
                  <td style={{ padding: "10px 12px" }}><FundBadge value={item.pe_ratio} good={0} warn={30} /></td>
                  <td style={{ padding: "10px 12px" }}><FundBadge value={item.roe_pct} suffix="%" good={15} warn={8} /></td>
                  <td style={{ padding: "10px 12px" }}>
                    {item.market_cap_cr != null ? (
                      <span style={{ fontSize: "0.72rem", fontWeight: 500, color: "var(--text-secondary)" }}>
                        {item.market_cap_cr >= 10000 ? `${(item.market_cap_cr / 10000).toFixed(1)}L Cr` : `${Math.round(item.market_cap_cr)} Cr`}
                      </span>
                    ) : <span style={{ color: "var(--text-dim)", fontSize: "0.72rem" }}>-</span>}
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <DataBadge auth={item.data_authenticity} />
                    <StatusBadge status={item.status} />
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <LevelsTooltip item={item} />
                  </td>
                  <td style={{ padding: "10px 12px", color: "var(--text-secondary)", fontSize: "0.76rem", whiteSpace: "nowrap" }}>
                    {fmtDate(item.signal_first_detected_at || item.created_at)}
                  </td>
                  <td style={{ padding: "10px 12px", fontSize: "0.74rem", whiteSpace: "nowrap" }}>
                    <span
                      title={`Analysis last refreshed: ${fmtDateTime(item.signals_updated_at ?? item.created_at)}`}
                      style={{
                        color: "var(--text-secondary)",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        cursor: "default",
                      }}
                    >
                      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0, opacity: 0.6 }}>
                        <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.2"/>
                        <path d="M6 3.5V6L7.5 7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
                      </svg>
                      {fmtDateTime(item.signals_updated_at ?? item.created_at)}
                    </span>
                  </td>
                  <td style={{ padding: "10px 12px" }}>
                    <button
                      onClick={() => setReasoningItem(item)}
                      style={{
                        background: "rgba(41, 98, 255, 0.12)", border: "1px solid rgba(41, 98, 255, 0.3)",
                        borderRadius: 6, padding: "5px 10px", cursor: "pointer",
                        color: "var(--accent)", fontSize: "0.72rem", fontWeight: 600,
                        whiteSpace: "nowrap",
                      }}
                    >
                      View Evidence
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AnimatePresence>
        {reasoningItem && <ReasoningModal item={reasoningItem} onClose={() => setReasoningItem(null)} />}
      </AnimatePresence>
    </div>
  );
}
