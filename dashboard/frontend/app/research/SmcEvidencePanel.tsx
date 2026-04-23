"use client";
/**
 * Phase 2 — Real SMC Evidence Panel
 * ==================================
 * Replaces generic "RSI ~63" prose with structured citations of the actual
 * Order Block / FVG / Sweep / Structure / Displacement that justified the signal.
 *
 * Used inside ReasoningModal on Swing & LongTerm idea cards.
 */
import type { SmcEvidence } from "@/lib/api";

const TF_LABEL: Record<string, string> = { "1D": "Daily", "1W": "Weekly" };
const FACTOR_LABEL: Record<string, string> = {
  weekly_trend: "Weekly Trend",
  daily_structure: "Daily Structure",
  ob: "Order Block",
  fvg: "Fair Value Gap",
  rs: "Relative Strength",
  volume: "Volume",
  rr: "Risk/Reward",
  sweep: "Liquidity Sweep",
  displacement: "Displacement",
  weekly_structure: "Weekly Structure",
};

function fmtPrice(n: number): string {
  return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function Chip({
  label,
  value,
  tone = "blue",
}: {
  label: string;
  value: string;
  tone?: "blue" | "green" | "amber" | "purple" | "red";
}) {
  const palette: Record<string, { fg: string; bg: string; border: string }> = {
    blue:   { fg: "#5b9cf6", bg: "rgba(91,156,246,0.10)",  border: "rgba(91,156,246,0.35)" },
    green:  { fg: "#00d18c", bg: "rgba(0,209,140,0.10)",   border: "rgba(0,209,140,0.35)" },
    amber:  { fg: "#f0c060", bg: "rgba(240,192,96,0.10)",  border: "rgba(240,192,96,0.35)" },
    purple: { fg: "#b07cf0", bg: "rgba(176,124,240,0.10)", border: "rgba(176,124,240,0.35)" },
    red:    { fg: "#ef6868", bg: "rgba(239,104,104,0.10)", border: "rgba(239,104,104,0.35)" },
  };
  const p = palette[tone];
  return (
    <div
      style={{
        display: "inline-flex", alignItems: "baseline", gap: 6,
        padding: "5px 10px", background: p.bg, color: p.fg,
        border: `1px solid ${p.border}`, borderRadius: 6, fontSize: "0.75rem",
        fontWeight: 600,
      }}
    >
      <span style={{ opacity: 0.75, fontWeight: 500 }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function SmcEvidencePanel({ evidence }: { evidence: SmcEvidence | null | undefined }) {
  if (!evidence) return null;

  const tfLabel = TF_LABEL[evidence.timeframe] || evidence.timeframe;
  const hasAny =
    evidence.ob_zone ||
    evidence.fvg_range ||
    evidence.sweep_level ||
    evidence.structure !== "NONE" ||
    evidence.displacement_atr_mult > 0 ||
    Object.keys(evidence.confluence_breakdown || {}).length > 0;

  if (!hasAny) return null;

  // Confluence chips, sorted high→low so strongest factors lead.
  const confluenceEntries = Object.entries(evidence.confluence_breakdown || {})
    .filter(([, v]) => Number(v) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]));

  return (
    <div>
      <div
        style={{
          fontSize: "0.68rem",
          fontWeight: 700,
          color: "#5b9cf6",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        Real SMC Evidence
        <span
          style={{
            fontSize: "0.6rem",
            color: "var(--text-dim)",
            background: "rgba(255,255,255,0.05)",
            padding: "1px 6px",
            borderRadius: 4,
            letterSpacing: 0,
          }}
        >
          {tfLabel} TF
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          padding: "10px 12px",
          background: "rgba(91,156,246,0.04)",
          borderRadius: 8,
          border: "1px solid rgba(91,156,246,0.15)",
        }}
      >
        {evidence.structure !== "NONE" && (
          <Chip
            label={`${evidence.structure_dir} ${evidence.structure}`}
            value={
              evidence.structure_level != null ? `@ ₹${fmtPrice(evidence.structure_level)}` : ""
            }
            tone={evidence.structure_dir === "BULLISH" ? "green" : "red"}
          />
        )}
        {evidence.ob_zone && (
          <Chip
            label="OB"
            value={`₹${fmtPrice(evidence.ob_zone.low)} – ₹${fmtPrice(evidence.ob_zone.high)}`}
            tone="blue"
          />
        )}
        {evidence.fvg_range && (
          <Chip
            label="FVG"
            value={`₹${fmtPrice(evidence.fvg_range.low)} – ₹${fmtPrice(evidence.fvg_range.high)}`}
            tone="purple"
          />
        )}
        {evidence.sweep_level && (
          <Chip
            label={`Sweep ${evidence.sweep_level.side}`}
            value={`₹${fmtPrice(evidence.sweep_level.price)}`}
            tone="amber"
          />
        )}
        {evidence.displacement_atr_mult > 0 && (
          <Chip
            label="Displacement"
            value={`${evidence.displacement_atr_mult.toFixed(2)}× ATR`}
            tone={evidence.displacement_atr_mult >= 1.5 ? "green" : "blue"}
          />
        )}
      </div>

      {confluenceEntries.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              fontSize: "0.62rem",
              color: "var(--text-dim)",
              textTransform: "uppercase",
              letterSpacing: "0.06em",
              marginBottom: 4,
            }}
          >
            Confluence Score Breakdown
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {confluenceEntries.map(([k, v]) => (
              <span
                key={k}
                style={{
                  fontSize: "0.7rem",
                  padding: "3px 8px",
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 4,
                  color: "var(--text-secondary)",
                }}
              >
                {FACTOR_LABEL[k] || k}{" "}
                <span style={{ color: "#5b9cf6", fontWeight: 600 }}>+{v}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
