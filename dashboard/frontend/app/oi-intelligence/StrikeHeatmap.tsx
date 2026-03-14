"use client";
import { useState, useMemo } from "react";
import { Layers, PhoneCall, HandMetal } from "lucide-react";
import { fmtOI, type StrikeHeatmapEntry } from "./types";

/* -- Status helpers ------------------------------------------------- */
type StatusKind = "buildup" | "short_cover" | "unwind" | "normal";

function parseStatus(s: string): StatusKind {
  const u = s?.toLowerCase() || "";
  if (u.includes("short_cover") || u.includes("short cover")) return "short_cover";
  if (u.includes("unwind"))  return "unwind";
  if (u.includes("buildup")) return "buildup";
  return "normal";
}

/* Bar fill -- alpha scales with OI magnitude (0.30 -> 0.95) */
function barColor(kind: StatusKind, side: "ce" | "pe", ratio: number): string {
  const a = 0.3 + ratio * 0.65;
  if (kind === "short_cover") return `rgba(0,224,150,${a})`;
  if (kind === "unwind")      return `rgba(0,180,130,${a * 0.75})`;
  if (kind === "buildup")     return side === "ce" ? `rgba(255,71,87,${a})` : `rgba(255,165,2,${a})`;
  return `rgba(120,130,160,${0.2 + ratio * 0.25})`;
}

/* Proximity category relative to ATM */
type Proximity = "atm" | "near1" | "near2" | "far";

/* Per-strike PCR badge */
function PcrBadge({ pcr }: { pcr: number }) {
  if (pcr <= 0) return null;
  const bullish = pcr >= 1.3;
  const bearish = pcr <= 0.7;
  const color  = bullish ? "rgba(0,224,150,1)"    : bearish ? "rgba(255,71,87,1)"    : "rgba(170,180,210,0.8)";
  const bg     = bullish ? "rgba(0,224,150,0.08)"  : bearish ? "rgba(255,71,87,0.08)"  : "rgba(120,130,160,0.08)";
  const border = bullish ? "rgba(0,224,150,0.25)"  : bearish ? "rgba(255,71,87,0.25)"  : "rgba(120,130,160,0.2)";
  const arrow  = bullish ? "+" : bearish ? "-" : "";
  return (
    <span style={{
      fontSize: "0.55rem", fontWeight: 700, padding: "1px 4px",
      borderRadius: 3, background: bg, color, border: `1px solid ${border}`,
      whiteSpace: "nowrap", letterSpacing: "0.03em",
    }}>{arrow} PCR {pcr.toFixed(1)}</span>
  );
}

/* Trade implication badge (works for both NIFTY and BANKNIFTY) */
function tradeImplication(
  e: StrikeHeatmapEntry,
  isAbove: boolean,
  prox: Proximity,
): { label: string; color: string; bg: string } | null {
  const ce  = parseStatus(e.ce_status);
  const pe  = parseStatus(e.pe_status);
  const R   = "#ff4757", G = "#00e096", Y = "rgba(255,165,2,1)";
  const r   = (l: string, c: string, b: string) => ({ label: l, color: c, bg: b });
  const near = prox === "near1" || prox === "near2";

  if (prox === "atm") {
    if (ce === "buildup" && pe === "buildup") return r("PINNED",      Y, "rgba(255,165,2,0.1)");
    if (ce === "short_cover")                 return r("BEARS EXIT",  G, "rgba(0,224,150,0.08)");
    if (pe === "short_cover")                 return r("LONGS EXIT",  R, "rgba(255,71,87,0.08)");
    if (ce === "unwind" && pe === "unwind")   return r("EXPIRY TRAP", Y, "rgba(255,165,2,0.08)");
    return null;
  }
  if (isAbove) {
    if (ce === "buildup")     return r(near ? "NEXT RESIST"    : "RESIST",          R, "rgba(255,71,87,0.08)");
    if (ce === "short_cover") return r("SC -- BULL",   G, "rgba(0,224,150,0.08)");
    if (ce === "unwind")      return r("BREAK POSSIBLE", G, "rgba(0,224,150,0.07)");
    return null;
  } else {
    if (pe === "buildup")     return r(near ? "NEXT SUPPORT"   : "SUPPORT",         G, "rgba(0,224,150,0.08)");
    if (pe === "short_cover") return r("SC -- BEAR",   R, "rgba(255,71,87,0.08)");
    if (pe === "unwind")      return r("SUPPORT WEAK", Y, "rgba(255,165,2,0.08)");
    return null;
  }
}

/* CE change chip -- rise means more resistance (bearish signal) */
function CeChangeChip({ pct }: { pct: number }) {
  if (!pct || isNaN(pct)) return null;
  const abs = Math.abs(pct * 100);
  if (abs < 0.5) return null;
  const up = pct > 0;
  return (
    <span style={{ fontSize: "0.52rem", color: up ? "rgba(255,71,87,0.9)" : "rgba(0,200,130,0.9)", fontWeight: 700, whiteSpace: "nowrap" }}>
      {up ? "+" : "-"}{abs.toFixed(1)}%
    </span>
  );
}

/* PE change chip -- rise means more support (bullish signal) */
function PeChangeChip({ pct }: { pct: number }) {
  if (!pct || isNaN(pct)) return null;
  const abs = Math.abs(pct * 100);
  if (abs < 0.5) return null;
  const up = pct > 0;
  return (
    <span style={{ fontSize: "0.52rem", color: up ? "rgba(0,200,130,0.9)" : "rgba(255,165,2,0.9)", fontWeight: 700, whiteSpace: "nowrap" }}>
      {up ? "+" : "-"}{abs.toFixed(1)}%
    </span>
  );
}

function DomBadge({ label }: { label: string }) {
  return (
    <span style={{
      fontSize: "0.55rem", fontWeight: 700, letterSpacing: "0.05em",
      padding: "2px 5px", borderRadius: 3,
      background: "rgba(255,165,2,0.15)", color: "rgba(255,165,2,1)",
      border: "1px solid rgba(255,165,2,0.3)", whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

/* Proximity label badge shown in the strike column */
function ProxBadge({ prox }: { prox: Proximity }) {
  if (prox === "atm")   return (
    <span style={{
      fontSize: "0.52rem", fontWeight: 800, padding: "1px 4px", borderRadius: 3,
      background: "rgba(255,220,0,0.18)", color: "rgba(255,220,0,1)",
      border: "1px solid rgba(255,220,0,0.5)", letterSpacing: "0.06em",
    }}>ATM</span>
  );
  if (prox === "near1") return (
    <span style={{
      fontSize: "0.5rem", fontWeight: 700, padding: "1px 3px", borderRadius: 3,
      background: "rgba(100,180,255,0.12)", color: "rgba(100,180,255,0.95)",
      border: "1px solid rgba(100,180,255,0.35)",
    }}>+1</span>
  );
  if (prox === "near2") return (
    <span style={{
      fontSize: "0.5rem", fontWeight: 700, padding: "1px 3px", borderRadius: 3,
      background: "rgba(100,180,255,0.06)", color: "rgba(100,180,255,0.65)",
      border: "1px solid rgba(100,180,255,0.2)",
    }}>+2</span>
  );
  return null;
}

/* Row visual style keyed by proximity (same logic for both indices) */
function rowStyle(prox: Proximity, isWall: boolean, idx: number) {
  if (prox === "atm")   return { bg: "rgba(255,220,0,0.055)",  border: "1px solid rgba(255,220,0,0.55)",  accentColor: "rgba(255,220,0,0.85)"  };
  if (prox === "near1") return { bg: "rgba(100,180,255,0.05)", border: "1px solid rgba(100,180,255,0.4)", accentColor: "rgba(100,180,255,0.7)"  };
  if (prox === "near2") return { bg: "rgba(100,180,255,0.02)", border: "1px solid rgba(100,180,255,0.2)", accentColor: "rgba(100,180,255,0.35)" };
  if (isWall)           return { bg: "rgba(255,165,2,0.03)",   border: "1px solid rgba(255,165,2,0.2)",  accentColor: "rgba(255,165,2,0.6)"   };
  return {
    bg: idx % 2 === 0 ? "rgba(255,255,255,0.01)" : "transparent",
    border: "1px solid transparent",
    accentColor: "transparent",
  };
}

/* Strike number color keyed by proximity */
function strikeColor(prox: Proximity): string {
  if (prox === "atm")   return "rgba(255,220,0,1)";
  if (prox === "near1") return "rgba(100,180,255,0.95)";
  if (prox === "near2") return "rgba(100,180,255,0.72)";
  return "var(--text-primary)";
}

/* ================================================================
   MAIN COMPONENT
   Works identically for NIFTY and BANKNIFTY -- step size is
   auto-inferred from strike differences for each underlying.
================================================================= */
export function StrikeHeatmap({ entries }: { entries: StrikeHeatmapEntry[] }) {
  const [filter, setFilter] = useState<string>("ALL");

  const underlyings = useMemo(() => [...new Set(entries.map(e => e.underlying))], [entries]);
  const filtered    = useMemo(
    () => filter === "ALL" ? entries : entries.filter(e => e.underlying === filter),
    [entries, filter],
  );
  const sorted = useMemo(
    () => [...filtered].sort((a, b) =>
      a.underlying !== b.underlying ? a.underlying.localeCompare(b.underlying) : a.strike - b.strike,
    ),
    [filtered],
  );

  /* ATM + step size per underlying (auto-detected -- works for any index) */
  const atmInfo = useMemo(() => {
    const info: Record<string, { atm: number; step: number; spot: number }> = {};
    for (const ul of underlyings) {
      const rows  = sorted.filter(e => e.underlying === ul);
      const spot  = rows.find(e => (e.spot ?? 0) > 0)?.spot ?? 0;
      if (!spot || rows.length < 2) continue;
      const diffs = rows.slice(1).map((r, i) => r.strike - rows[i].strike).filter(d => d > 0);
      const step  = diffs.length ? Math.min(...diffs) : (ul.includes("BANK") ? 100 : 50);
      let best    = rows[0];
      for (const r of rows) if (Math.abs(r.strike - spot) < Math.abs(best.strike - spot)) best = r;
      info[ul] = { atm: best.strike, step, spot };
    }
    return info;
  }, [sorted, underlyings]);

  /* Build proximity map -- keyed by "UNDERLYING-STRIKE" string */
  const proxMap = useMemo(() => {
    const m: Record<string, Proximity> = {};
    for (const [ul, { atm, step }] of Object.entries(atmInfo)) {
      for (const e of sorted.filter(r => r.underlying === ul)) {
        const dist = Math.abs(e.strike - atm);
        const key  = `${ul}-${e.strike}`;
        if (dist === 0)          m[key] = "atm";
        else if (dist <= step)   m[key] = "near1";
        else if (dist <= step*2) m[key] = "near2";
        else                     m[key] = "far";
      }
    }
    return m;
  }, [sorted, atmInfo]);

  /* Normalise bar widths globally */
  const maxOI = useMemo(() => Math.max(1, ...sorted.flatMap(e => [e.ce_oi, e.pe_oi])), [sorted]);

  /* Top 2 CE and PE walls per underlying */
  const walls = useMemo(() => {
    const ce = new Set<string>(), pe = new Set<string>();
    for (const ul of underlyings) {
      const rows = sorted.filter(e => e.underlying === ul);
      [...rows].sort((a, b) => b.ce_oi - a.ce_oi).slice(0, 2).forEach(r => ce.add(`${ul}-${r.strike}`));
      [...rows].sort((a, b) => b.pe_oi - a.pe_oi).slice(0, 2).forEach(r => pe.add(`${ul}-${r.strike}`));
    }
    return { ce, pe };
  }, [sorted, underlyings]);

  /* Group rows per underlying for section dividers */
  const groups = useMemo(() =>
    filter !== "ALL"
      ? [{ ul: filter, rows: sorted }]
      : underlyings.map(ul => ({ ul, rows: sorted.filter(e => e.underlying === ul) })),
    [sorted, underlyings, filter],
  );

  return (
    <div className="glass" style={{ padding: 20, overflow: "hidden" }}>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: "0.1em", textTransform: "uppercase", fontWeight: 600 }}>
          <Layers size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "middle" }} />
          STRIKE HEATMAP
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button onClick={() => setFilter("ALL")}
            className={`badge ${filter === "ALL" ? "badge-long" : "badge-neutral"}`}
            style={{ cursor: "pointer" }}>ALL</button>
          {underlyings.map(u => (
            <button key={u} onClick={() => setFilter(u)}
              className={`badge ${filter === u ? "badge-long" : "badge-neutral"}`}
              style={{ cursor: "pointer" }}>{u}</button>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 14, fontSize: "0.6rem", color: "var(--text-dim)" }}>
        {[
          { color: "rgba(255,71,87,0.8)",   label: "CE Buildup -- resistance ceiling" },
          { color: "rgba(255,165,2,0.8)",    label: "PE Buildup -- support floor" },
          { color: "rgba(0,224,150,0.8)",    label: "SC / Unwind -- position closing" },
          { color: "rgba(120,130,160,0.35)", label: "Normal / Low activity" },
        ].map(({ color, label }) => (
          <span key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 14, height: 8, borderRadius: 2, background: color, display: "inline-block" }} />
            {label}
          </span>
        ))}
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <DomBadge label="MAX OI" />  Heaviest wall strikes
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ display: "inline-flex", gap: 3 }}>
            <ProxBadge prox="atm" />
            <ProxBadge prox="near1" />
            <ProxBadge prox="near2" />
          </span>
          ATM zone (2 strikes each side, both indices)
        </span>
      </div>

      {/* Column headers */}
      <div style={{
        display: "grid", gridTemplateColumns: "6px 1fr 116px 1fr",
        gap: 4, marginBottom: 6,
        fontSize: "0.58rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.06em",
      }}>
        <div />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 5, paddingRight: 8 }}>
          <PhoneCall size={10} /> CALL (CE) -- bearish pressure
        </div>
        <div style={{ textAlign: "center" }}>STRIKE</div>
        <div style={{ display: "flex", alignItems: "center", gap: 5, paddingLeft: 8 }}>
          PUT (PE) -- bullish support <HandMetal size={10} />
        </div>
      </div>

      {sorted.length === 0 ? (
        <div style={{ textAlign: "center", color: "var(--text-dim)", padding: 40 }}>No heatmap data available</div>
      ) : (
        <div style={{ maxHeight: 560, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
          {groups.map(({ ul, rows }) => (
            <div key={ul}>

              {/* Per-underlying section header -- visible in ALL mode only */}
              {filter === "ALL" && (
                <div style={{
                  fontSize: "0.6rem", fontWeight: 700, letterSpacing: "0.1em",
                  color: "var(--text-secondary)", textTransform: "uppercase",
                  padding: "6px 4px 3px 10px", borderBottom: "1px solid rgba(255,255,255,0.06)",
                  marginBottom: 3,
                }}>
                  {ul}
                  {atmInfo[ul] && (
                    <span style={{ marginLeft: 8, fontWeight: 400, color: "var(--text-dim)", textTransform: "none", letterSpacing: 0 }}>
                      spot {atmInfo[ul].spot.toFixed(0)} &nbsp;|&nbsp; ATM {atmInfo[ul].atm} &nbsp;|&nbsp; step {atmInfo[ul].step}
                    </span>
                  )}
                </div>
              )}

              {rows.map((e, i) => {
                const key    = `${e.underlying}-${e.strike}`;
                const prox   = proxMap[key] ?? "far";
                const isWall = walls.ce.has(key) || walls.pe.has(key);
                const rs     = rowStyle(prox, isWall, i);

                const ceKind  = parseStatus(e.ce_status);
                const peKind  = parseStatus(e.pe_status);
                const ceRatio = e.ce_oi / maxOI;
                const peRatio = e.pe_oi / maxOI;
                const cePct   = Math.max(4, Math.round(ceRatio * 100));
                const pePct   = Math.max(4, Math.round(peRatio * 100));
                const spot    = e.spot ?? atmInfo[ul]?.spot ?? 0;
                const isAbove = spot > 0 && e.strike > spot;
                const distPts = spot > 0 ? e.strike - spot : 0;
                const pcr     = e.strike_pcr ?? (e.ce_oi > 0 ? +(e.pe_oi / e.ce_oi).toFixed(2) : 0);
                const impl    = tradeImplication(e, isAbove, prox);

                return (
                  <div key={key + i} style={{
                    display: "grid", gridTemplateColumns: "6px 1fr 116px 1fr",
                    gap: 4, alignItems: "center",
                    background: rs.bg, borderRadius: 5, border: rs.border,
                    padding: "5px 4px 5px 0", overflow: "hidden",
                  }}>

                    {/* Left accent bar (color indicates proximity tier) */}
                    <div style={{
                      alignSelf: "stretch", borderRadius: "4px 0 0 4px",
                      background: rs.accentColor,
                    }} />

                    {/* CE side -- right-aligned, bar grows right->left */}
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        {walls.ce.has(key) && <DomBadge label="MAX OI" />}
                        {ceKind !== "normal" && (
                          <span style={{
                            fontSize: "0.58rem", fontWeight: 600, whiteSpace: "nowrap",
                            color: ceKind === "buildup"     ? "rgba(255,71,87,0.9)"
                                 : ceKind === "short_cover" ? "rgba(0,224,150,0.9)"
                                 :                            "rgba(0,180,130,0.8)",
                          }}>
                            {ceKind === "buildup"     ? "Resistance"
                           : ceKind === "short_cover" ? "SC Active"
                           :                            "Unwind"}
                          </span>
                        )}
                        <span style={{ fontSize: "0.68rem", fontFamily: "monospace", fontWeight: 600, color: "var(--text-primary)", whiteSpace: "nowrap" }}>
                          {fmtOI(e.ce_oi)}
                        </span>
                        <CeChangeChip pct={e.ce_change} />
                      </div>
                      <div style={{ width: "100%", height: 6, borderRadius: 3, background: "rgba(255,255,255,0.05)", display: "flex", justifyContent: "flex-end", overflow: "hidden" }}>
                        <div style={{ width: `${cePct}%`, height: "100%", background: barColor(ceKind, "ce", ceRatio), borderRadius: "3px 0 0 3px", transition: "width 0.35s ease" }} />
                      </div>
                    </div>

                    {/* Strike center column */}
                    <div style={{ textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <ProxBadge prox={prox} />
                        <span style={{ fontSize: "0.72rem", fontWeight: 700, fontFamily: "monospace", lineHeight: 1, color: strikeColor(prox) }}>
                          {e.strike}
                        </span>
                      </div>
                      {filter === "ALL" && (
                        <div style={{ fontSize: "0.5rem", color: "var(--text-dim)", lineHeight: 1 }}>{e.underlying}</div>
                      )}
                      {spot > 0 && prox !== "atm" && (
                        <div style={{
                          fontSize: "0.5rem", fontFamily: "monospace", lineHeight: 1,
                          color: isAbove ? "rgba(255,100,100,0.7)" : "rgba(100,220,160,0.7)",
                        }}>
                          {distPts > 0 ? "+" : ""}{Math.round(distPts)} pts
                        </div>
                      )}
                      {pcr > 0 && <PcrBadge pcr={pcr} />}
                      {impl && (
                        <span style={{
                          fontSize: "0.5rem", fontWeight: 700, padding: "1px 4px",
                          borderRadius: 3, color: impl.color, background: impl.bg,
                          border: `1px solid ${impl.color}33`, whiteSpace: "nowrap", letterSpacing: "0.03em",
                        }}>{impl.label}</span>
                      )}
                    </div>

                    {/* PE side -- left-aligned, bar grows left->right */}
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <PeChangeChip pct={e.pe_change} />
                        <span style={{ fontSize: "0.68rem", fontFamily: "monospace", fontWeight: 600, color: "var(--text-primary)", whiteSpace: "nowrap" }}>
                          {fmtOI(e.pe_oi)}
                        </span>
                        {peKind !== "normal" && (
                          <span style={{
                            fontSize: "0.58rem", fontWeight: 600, whiteSpace: "nowrap",
                            color: peKind === "buildup"     ? "rgba(255,165,2,0.9)"
                                 : peKind === "short_cover" ? "rgba(0,224,150,0.9)"
                                 :                            "rgba(0,180,130,0.8)",
                          }}>
                            {peKind === "buildup"     ? "Support"
                           : peKind === "short_cover" ? "SC Active"
                           :                            "Unwind"}
                          </span>
                        )}
                        {walls.pe.has(key) && <DomBadge label="MAX OI" />}
                      </div>
                      <div style={{ width: "100%", height: 6, borderRadius: 3, background: "rgba(255,255,255,0.05)", overflow: "hidden" }}>
                        <div style={{ width: `${pePct}%`, height: "100%", background: barColor(peKind, "pe", peRatio), borderRadius: "0 3px 3px 0", transition: "width 0.35s ease" }} />
                      </div>
                    </div>

                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
