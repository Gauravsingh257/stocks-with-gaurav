"use client";

/**
 * AISummaryPanel — top-of-page intelligence digest.
 *
 * Renders the headline, market bias, and the top 3 ranked opportunities
 * with their probability + quality + RR.
 */

import { motion } from "framer-motion";
import { Brain, Target, TrendingUp, TrendingDown, Activity } from "lucide-react";
import type { AISummaryPayload, SummaryCard } from "../_lib/useTerminalSummary";

interface Props {
  data: AISummaryPayload | null;
  loading?: boolean;
  onPickSymbol?: (symbol: string) => void;
}

const BIAS_COLOR: Record<string, { bg: string; fg: string; border: string }> = {
  BULLISH: { bg: "rgba(0,224,150,0.12)", fg: "#00e096", border: "rgba(0,224,150,0.4)" },
  BEARISH: { bg: "rgba(255,71,87,0.12)", fg: "#ff4757", border: "rgba(255,71,87,0.4)" },
  MIXED: { bg: "rgba(255,165,2,0.12)", fg: "#ffa502", border: "rgba(255,165,2,0.4)" },
  NEUTRAL: { bg: "rgba(120,140,180,0.12)", fg: "#8899bb", border: "rgba(120,140,180,0.4)" },
};

const RISK_COLOR: Record<string, string> = {
  LOW: "#00e096",
  MED: "#ffa502",
  HIGH: "#ff4757",
};

export default function AISummaryPanel({ data, loading, onPickSymbol }: Props) {
  const bias = (data?.market_bias || "NEUTRAL").toUpperCase();
  const biasStyle = BIAS_COLOR[bias] ?? BIAS_COLOR.NEUTRAL;

  return (
    <motion.section
      layout
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{
        position: "relative",
        padding: "20px 22px",
        borderRadius: 18,
        border: "1px solid var(--border)",
        background:
          "linear-gradient(135deg, rgba(0,212,255,0.06), rgba(0,224,150,0.04) 60%, rgba(255,255,255,0.01))",
        marginBottom: 18,
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <Brain size={16} color="#00d4ff" />
        <span
          style={{
            fontSize: "0.62rem",
            fontWeight: 700,
            letterSpacing: 1.2,
            color: "var(--accent)",
            textTransform: "uppercase",
          }}
        >
          AI Intelligence
        </span>
        <span
          style={{
            fontSize: "0.6rem",
            fontWeight: 700,
            letterSpacing: 1,
            padding: "2px 8px",
            borderRadius: 999,
            background: biasStyle.bg,
            color: biasStyle.fg,
            border: `1px solid ${biasStyle.border}`,
          }}
        >
          {bias}
        </span>
        {data && (
          <span style={{ marginLeft: "auto", fontSize: "0.66rem", color: "var(--text-dim)" }}>
            {data.totals.count} setups · {data.totals.long}↑ / {data.totals.short}↓ · avg{" "}
            {data.totals.avg_quality.toFixed(1)}/10
          </span>
        )}
      </div>

      <h2
        style={{
          margin: "0 0 14px",
          fontSize: "0.98rem",
          fontWeight: 600,
          color: "var(--text-primary)",
          lineHeight: 1.45,
        }}
      >
        {loading && !data
          ? "Reading the tape…"
          : data?.headline ??
            "Engine warming up — no setups generated yet."}
      </h2>

      {data && data.top_trades.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
            gap: 10,
          }}
        >
          {data.top_trades.map((t, idx) => (
            <TopTradeChip key={t.symbol} trade={t} rank={idx + 1} onClick={() => onPickSymbol?.(t.symbol)} />
          ))}
        </div>
      )}
    </motion.section>
  );
}

function TopTradeChip({ trade, rank, onClick }: { trade: SummaryCard; rank: number; onClick?: () => void }) {
  const isLong = trade.direction === "LONG";
  const dirColor = isLong ? "#00e096" : "#ff4757";
  const DirIcon = isLong ? TrendingUp : TrendingDown;
  const riskColor = RISK_COLOR[trade.risk_level] ?? "#8899bb";
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "stretch",
        gap: 12,
        padding: "10px 12px",
        borderRadius: 12,
        border: "1px solid var(--border)",
        background: "rgba(255,255,255,0.025)",
        color: "var(--text-primary)",
        textAlign: "left",
        cursor: onClick ? "pointer" : "default",
        transition: "background 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.05)";
        e.currentTarget.style.borderColor = "var(--accent-dim)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.025)";
        e.currentTarget.style.borderColor = "var(--border)";
      }}
    >
      <div
        style={{
          fontSize: "0.6rem",
          fontWeight: 800,
          color: "var(--accent)",
          minWidth: 18,
          paddingTop: 2,
        }}
      >
        #{rank}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: "0.84rem", fontWeight: 800, letterSpacing: 0.3 }}>{trade.symbol}</span>
          <DirIcon size={11} color={dirColor} />
          <span
            style={{
              fontSize: "0.55rem",
              fontWeight: 700,
              color: dirColor,
              letterSpacing: 0.6,
            }}
          >
            {trade.direction}
          </span>
          <span
            style={{
              marginLeft: "auto",
              fontSize: "0.58rem",
              fontWeight: 700,
              letterSpacing: 0.5,
              padding: "2px 6px",
              borderRadius: 999,
              background: `${riskColor}1f`,
              color: riskColor,
              border: `1px solid ${riskColor}55`,
            }}
          >
            {trade.risk_level}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: "0.66rem",
            color: "var(--text-secondary)",
          }}
        >
          <Target size={10} color="#00d4ff" />
          <span>{trade.probability}% prob</span>
          <span style={{ color: "var(--text-dim)" }}>·</span>
          <span>{trade.quality_score?.toFixed(1) ?? "—"}/10</span>
          <span style={{ color: "var(--text-dim)" }}>·</span>
          <span>{trade.rr ? `${trade.rr.toFixed(2)}R` : "—"}</span>
          <Activity size={9} color="var(--text-dim)" style={{ marginLeft: "auto" }} />
          <span style={{ color: "var(--text-dim)" }}>{trade.expected_move_time}</span>
        </div>
      </div>
    </button>
  );
}
