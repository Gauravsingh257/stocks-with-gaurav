"use client";

import Link from "next/link";
import type { StockAnalysis } from "@/lib/api";
import { recommendationColors } from "@/utils/calculateConfidence";

function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "-";
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "-";
  return `${Math.round(value)}%`;
}

function setupLabel(horizon: string, setupType: string): string {
  if (setupType && setupType !== "No Valid SMC Setup") return setupType.replace(/_/g, " ");
  if (horizon === "LONGTERM") return "Long-term Watch";
  return "Swing Watch";
}

export default function StockCard({
  analysis,
  compact = false,
  badge,
}: {
  analysis: StockAnalysis;
  compact?: boolean;
  badge?: string;
}) {
  const colors = recommendationColors(analysis.recommendation);
  const entry =
    analysis.entry_zone && analysis.entry_zone.length >= 2
      ? `${money(analysis.entry_zone[0])}–${money(analysis.entry_zone[1])}`
      : "-";

  return (
    <div
      className="glass"
      style={{
        padding: compact ? 14 : 18,
        display: "grid",
        gap: 12,
        border: `1px solid ${colors.border}`,
        background: compact ? "rgba(255,255,255,0.035)" : "linear-gradient(135deg, rgba(0,212,255,0.05), rgba(255,255,255,0.025))",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div>
          <Link
            href={`/stock/${encodeURIComponent(analysis.symbol)}`}
            style={{ color: "var(--text-primary)", textDecoration: "none", fontWeight: 800, fontSize: compact ? "1rem" : "1.15rem" }}
          >
            {analysis.name || analysis.symbol}
          </Link>
          <div style={{ color: "var(--text-secondary)", fontSize: "0.72rem", marginTop: 2 }}>
            NSE:{analysis.symbol}
            {analysis.fundamentals?.sector ? ` · ${analysis.fundamentals.sector}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 5, alignItems: "flex-end" }}>
          {badge && (
            <span style={{ fontSize: "0.68rem", padding: "2px 8px", borderRadius: 999, background: "rgba(245,158,11,0.16)", color: "var(--warning)", border: "1px solid rgba(245,158,11,0.28)", fontWeight: 700 }}>
              {badge}
            </span>
          )}
          <span style={{ fontSize: "0.7rem", padding: "3px 9px", borderRadius: 999, background: colors.bg, color: colors.fg, border: `1px solid ${colors.border}`, fontWeight: 800 }}>
            {analysis.recommendation}
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
        <Metric label="CMP" value={money(analysis.cmp)} />
        <Metric label="Entry Zone" value={entry} />
        <Metric label="Stop Loss" value={money(analysis.stop_loss)} tone="danger" />
        <Metric label="Target" value={money(analysis.target)} tone="success" />
        <Metric label="R:R" value={analysis.risk_reward ? `1:${analysis.risk_reward.toFixed(1)}` : "-"} />
        <Metric label="Confidence" value={pct(analysis.confidence_score)} tone={analysis.confidence_score >= 70 ? "success" : analysis.confidence_score >= 50 ? "warning" : "danger"} />
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-dim)", fontSize: "0.66rem", marginBottom: 4 }}>
          <span>Conviction</span>
          <span>{pct(analysis.confidence_score)}</span>
        </div>
        <div style={{ height: 7, borderRadius: 999, background: "rgba(255,255,255,0.06)", overflow: "hidden" }}>
          <div
            style={{
              width: `${Math.max(0, Math.min(100, analysis.confidence_score))}%`,
              height: "100%",
              borderRadius: 999,
              background: analysis.confidence_score >= 70 ? "var(--success)" : analysis.confidence_score >= 50 ? "var(--warning)" : "var(--danger)",
            }}
          />
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ fontSize: "0.72rem", padding: "3px 8px", borderRadius: 6, background: "rgba(0,212,255,0.1)", color: "var(--accent)", border: "1px solid rgba(0,212,255,0.18)", fontWeight: 700 }}>
          {setupLabel(analysis.horizon, analysis.setup_type)}
        </span>
        {analysis.fundamentals?.pe_ratio != null && (
          <span style={{ color: "var(--text-secondary)", fontSize: "0.72rem" }}>PE {analysis.fundamentals.pe_ratio.toFixed(1)}</span>
        )}
        {analysis.fundamentals?.market_cap_cr != null && (
          <span style={{ color: "var(--text-secondary)", fontSize: "0.72rem" }}>
            MCap {Math.round(analysis.fundamentals.market_cap_cr).toLocaleString("en-IN")} Cr
          </span>
        )}
      </div>

      <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: compact ? "0.78rem" : "0.84rem", lineHeight: 1.55 }}>
        {analysis.reason}
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", paddingTop: 2 }}>
        <Link
          href={`/stock/${encodeURIComponent(analysis.symbol)}`}
          style={{
            textDecoration: "none",
            padding: "7px 11px",
            borderRadius: 8,
            border: "1px solid rgba(0,212,255,0.28)",
            background: "rgba(0,212,255,0.1)",
            color: "var(--accent)",
            fontSize: "0.74rem",
            fontWeight: 850,
          }}
        >
          Open Full Analysis
        </Link>
        <a
          href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(analysis.symbol)}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            textDecoration: "none",
            padding: "7px 11px",
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: "rgba(255,255,255,0.03)",
            color: "var(--text-secondary)",
            fontSize: "0.74rem",
            fontWeight: 750,
          }}
        >
          TradingView
        </a>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "success" | "danger" | "warning";
}) {
  const color =
    tone === "success" ? "var(--success)" : tone === "danger" ? "var(--danger)" : tone === "warning" ? "var(--warning)" : "var(--text-primary)";
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", background: "rgba(255,255,255,0.02)" }}>
      <div style={{ color: "var(--text-dim)", fontSize: "0.64rem", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 3 }}>
        {label}
      </div>
      <div style={{ color, fontWeight: 800, fontSize: "0.88rem" }}>{value}</div>
    </div>
  );
}
