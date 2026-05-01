"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowDownRight, ArrowUpRight, Bookmark, Check, ShieldCheck, Sparkles, Zap } from "lucide-react";
import MiniChart from "./MiniChart";
import DecisionBadge from "./DecisionBadge";
import LifecycleBar from "./LifecycleBar";
import type { Opportunity } from "../_lib/opportunity";
import { priceLabel, rrLabel } from "../_lib/opportunity";

interface Props {
  opp: Opportunity;
  onView: (opp: Opportunity) => void;
  onWatch: (opp: Opportunity) => void;
  onMarkTaken?: (opp: Opportunity) => Promise<void>;
  watched?: boolean;
  index?: number;
}

const GRADE_STYLES: Record<string, { bg: string; color: string; border: string }> = {
  "A+": { bg: "rgba(0,224,150,0.15)", color: "#00e096", border: "rgba(0,224,150,0.45)" },
  A: { bg: "rgba(0,212,255,0.15)", color: "#00d4ff", border: "rgba(0,212,255,0.45)" },
  B: { bg: "rgba(255,165,2,0.15)", color: "#ffa502", border: "rgba(255,165,2,0.45)" },
  C: { bg: "rgba(120,140,180,0.15)", color: "#8899bb", border: "rgba(120,140,180,0.4)" },
};

const STATUS_DOT: Record<string, string> = {
  Waiting: "#ffa502",
  Approaching: "#00d4ff",
  Tapped: "#00d4ff",
  Triggered: "#00e096",
  Running: "#00e096",
  TargetHit: "#00e096",
  StopHit: "#ff4757",
};

export default function OpportunityCard({ opp, onView, onWatch, onMarkTaken, watched, index = 0 }: Props) {
  const isBuy = opp.direction === "BUY";
  const grade = GRADE_STYLES[opp.grade] ?? GRADE_STYLES.B;
  const dirColor = isBuy ? "#00e096" : "#ff4757";
  const DirIcon = isBuy ? ArrowUpRight : ArrowDownRight;
  const [taken, setTaken] = useState(opp.taken ?? false);
  const [takingLoading, setTakingLoading] = useState(false);

  async function handleMarkTaken(e: React.MouseEvent) {
    e.stopPropagation();
    if (taken || takingLoading || !onMarkTaken) return;
    setTakingLoading(true);
    await onMarkTaken(opp);
    setTaken(true);
    setTakingLoading(false);
  }

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, delay: Math.min(index, 8) * 0.04, ease: [0.21, 0.5, 0.3, 1] }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="opp-card"
      style={{
        position: "relative",
        background:
          "linear-gradient(160deg, rgba(255,255,255,0.045) 0%, rgba(255,255,255,0.015) 60%, rgba(255,255,255,0.04) 100%)",
        border: "1px solid var(--border)",
        borderRadius: 18,
        padding: 18,
        backdropFilter: "blur(14px)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        minHeight: 320,
      }}
    >
      {/* Direction edge glow */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(circle at ${isBuy ? "0% 0%" : "100% 0%"}, ${dirColor}20, transparent 55%)`,
          pointerEvents: "none",
        }}
      />

      {/* Header */}
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10, position: "relative" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: "1.05rem", fontWeight: 800, color: "var(--text-primary)", letterSpacing: 0.3 }}>{opp.symbol}</span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 8px",
                borderRadius: 999,
                fontSize: "0.62rem",
                fontWeight: 700,
                letterSpacing: 0.6,
                background: `${dirColor}1f`,
                color: dirColor,
                border: `1px solid ${dirColor}55`,
              }}
            >
              <DirIcon size={11} />
              {opp.direction}
            </span>
          </div>
          <div style={{ marginTop: 4, fontSize: "0.66rem", color: "var(--text-dim)", display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  background: STATUS_DOT[opp.status] ?? "#8899bb",
                  boxShadow: `0 0 6px ${STATUS_DOT[opp.status] ?? "#8899bb"}`,
                }}
              />
              {opp.status}
            </span>
            <span>·</span>
            <span>Setup {opp.setup}</span>
            {opp.sector && (
              <>
                <span>·</span>
                <span style={{ textTransform: "uppercase", letterSpacing: 0.5 }}>{opp.sector}</span>
              </>
            )}
          </div>
        </div>
        <div
          style={{
            background: grade.bg,
            color: grade.color,
            border: `1px solid ${grade.border}`,
            padding: "4px 10px",
            borderRadius: 10,
            fontSize: "0.72rem",
            fontWeight: 800,
            letterSpacing: 0.5,
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
          }}
          title={`Confidence ${(opp.raw.confidence_score ?? 0).toFixed(0)}`}
        >
          <Sparkles size={12} /> {opp.grade}
        </div>
      </header>

      {/* Mini Chart */}
      <div style={{ position: "relative" }}>
        <MiniChart data={opp.spark} direction={opp.direction} height={70} />
        {opp.cmp != null && (
          <div
            style={{
              position: "absolute",
              top: 4,
              right: 6,
              fontSize: "0.62rem",
              color: "var(--text-secondary)",
              background: "rgba(8,13,26,0.7)",
              padding: "2px 6px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              backdropFilter: "blur(4px)",
            }}
          >
            CMP {priceLabel(opp.cmp)}
          </div>
        )}
      </div>

      {/* Levels */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        <Level label="Entry" value={priceLabel(opp.entry)} accent="var(--accent)" />
        <Level label="Stop" value={priceLabel(opp.stop)} accent="#ff4757" />
        <Level label="Target" value={priceLabel(opp.target)} accent="#00e096" />
        <Level label="R:R" value={rrLabel(opp.rr)} accent="var(--text-primary)" highlight />
      </div>

      {/* Score chips */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <ScoreChip ok={opp.scores.liquidity} label="Liquidity" />
        <ScoreChip ok={opp.scores.structure} label="Structure" />
        <ScoreChip ok={opp.scores.htf} label="HTF" />
        <ScoreChip
          ok={opp.scores.entryQuality === "ok"}
          warn={opp.scores.entryQuality === "warn"}
          label="Entry"
        />
      </div>

      {/* Phase 3 — Intelligence strip */}
      {opp.intelligence && <IntelStrip intel={opp.intelligence} />}

      {/* Lifecycle bar */}
      <LifecycleBar status={opp.status} />

      {/* Decision + Reasoning */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {opp.intelligence?.action && (
          <DecisionBadge action={opp.intelligence.action} conviction={opp.intelligence.conviction ?? "MEDIUM"} size="sm" />
        )}
        <p
          style={{
            fontSize: "0.74rem",
            color: "var(--text-secondary)",
            lineHeight: 1.5,
            margin: 0,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {opp.intelligence?.narrative || opp.reasoning}
        </p>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
        <button
          type="button"
          onClick={() => onView(opp)}
          style={{
            flex: 1,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
            padding: "9px 12px",
            borderRadius: 10,
            border: "1px solid var(--accent)",
            background: "linear-gradient(135deg, rgba(0,212,255,0.18), rgba(0,212,255,0.06))",
            color: "var(--accent)",
            fontSize: "0.74rem",
            fontWeight: 700,
            letterSpacing: 0.4,
            cursor: "pointer",
          }}
        >
          <Zap size={13} /> View Setup
        </button>
        {/* Mark Taken button — only shown when engine recommends BUY/STRONG BUY and handler provided */}
        {onMarkTaken && (opp.intelligence?.action === "BUY" || opp.intelligence?.action === "STRONG BUY") && (
          <button
            type="button"
            onClick={handleMarkTaken}
            disabled={taken || takingLoading}
            title={taken ? "Trade recorded" : "Mark as taken"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "9px 12px",
              borderRadius: 10,
              border: `1px solid ${taken ? "rgba(0,224,150,0.5)" : "rgba(0,224,150,0.3)"}`,
              background: taken ? "rgba(0,224,150,0.18)" : "rgba(0,224,150,0.07)",
              color: taken ? "#00e096" : "rgba(0,224,150,0.7)",
              fontSize: "0.72rem",
              fontWeight: 700,
              cursor: taken ? "default" : "pointer",
              opacity: takingLoading ? 0.6 : 1,
              transition: "all 0.2s",
            }}
          >
            <Check size={13} />
            {taken ? "Taken" : "Take"}
          </button>
        )}
        <button
          type="button"
          onClick={() => onWatch(opp)}
          aria-pressed={watched}
          title={watched ? "In watchlist" : "Add to watchlist"}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "9px 12px",
            borderRadius: 10,
            border: `1px solid ${watched ? "var(--accent)" : "var(--border)"}`,
            background: watched ? "rgba(0,212,255,0.15)" : "rgba(255,255,255,0.03)",
            color: watched ? "var(--accent)" : "var(--text-secondary)",
            fontSize: "0.74rem",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          <Bookmark size={13} fill={watched ? "currentColor" : "none"} />
        </button>
      </div>

      {/* Bottom shimmer accent */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 1,
          background: `linear-gradient(90deg, transparent, ${dirColor}, transparent)`,
          opacity: 0.6,
        }}
      />
      <ShieldCheck style={{ display: "none" }} />
    </motion.article>
  );
}

function Level({
  label,
  value,
  accent,
  highlight,
}: {
  label: string;
  value: string;
  accent: string;
  highlight?: boolean;
}) {
  return (
    <div
      style={{
        background: highlight ? "rgba(0,212,255,0.08)" : "rgba(255,255,255,0.025)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "6px 8px",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: "0.58rem", color: "var(--text-dim)", letterSpacing: 0.6, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "0.82rem", fontWeight: 700, color: accent, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{value}</div>
    </div>
  );
}

function IntelStrip({ intel }: { intel: NonNullable<Opportunity["intelligence"]> }) {
  const riskColor =
    intel.riskLevel === "LOW" ? "#00e096" : intel.riskLevel === "MED" ? "#ffa502" : "#ff4757";
  const probColor = intel.probability >= 75 ? "#00e096" : intel.probability >= 60 ? "#ffa502" : "#8899bb";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 1fr",
        gap: 6,
        padding: 8,
        borderRadius: 10,
        background: "linear-gradient(135deg, rgba(0,212,255,0.06), rgba(0,212,255,0.01))",
        border: "1px solid rgba(0,212,255,0.18)",
      }}
    >
      <IntelCell label="Probability" value={`${intel.probability}%`} color={probColor} />
      <IntelCell label="Quality" value={`${intel.qualityScore.toFixed(1)}/10`} color="#00d4ff" />
      <IntelCell label="Risk" value={intel.riskLevel} color={riskColor} />
      <div
        style={{
          gridColumn: "1 / -1",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          fontSize: "0.62rem",
          color: "var(--text-secondary)",
          marginTop: 2,
        }}
      >
        <span style={{ color: "var(--text-dim)" }}>≈ {intel.expectedMoveTime}</span>
        <span style={{ fontWeight: 700, letterSpacing: 0.5, color: probColor }}>{intel.expectedOutcome}</span>
      </div>
    </div>
  );
}

function IntelCell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: "0.55rem", color: "var(--text-dim)", letterSpacing: 0.6, textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "0.78rem", fontWeight: 800, color, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}>{value}</div>
    </div>
  );
}

function ScoreChip({ ok, warn, label }: { ok: boolean; warn?: boolean; label: string }) {
  const color = ok ? "#00e096" : warn ? "#ffa502" : "#ff4757";
  const bg = ok ? "rgba(0,224,150,0.1)" : warn ? "rgba(255,165,2,0.1)" : "rgba(255,71,87,0.1)";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "3px 8px",
        borderRadius: 999,
        fontSize: "0.64rem",
        fontWeight: 600,
        background: bg,
        color,
        border: `1px solid ${color}33`,
      }}
    >
      <span aria-hidden style={{ width: 5, height: 5, borderRadius: 999, background: color, boxShadow: `0 0 4px ${color}` }} />
      {label}
    </span>
  );
}
