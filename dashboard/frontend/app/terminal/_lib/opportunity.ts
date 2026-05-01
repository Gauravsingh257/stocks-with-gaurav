/**
 * Shared types and helpers for the AI Trade Opportunity Terminal.
 * Maps backend ResearchDecisionCard payloads into a normalized shape
 * that the premium card UI can render without ad-hoc transforms.
 */
import type { ResearchDecisionCard } from "@/lib/api";

export type Direction = "BUY" | "SELL";
export type SetupGrade = "A+" | "A" | "B" | "C";
export type SetupType = "A" | "B" | "C" | "D";
export type StrategyMode = "intraday" | "swing";
export type RiskMode = "conservative" | "aggressive";
export type WatchStatus = "Waiting" | "Approaching" | "Tapped" | "Triggered" | "Running" | "TargetHit" | "StopHit";
export type RiskLevel = "LOW" | "MED" | "HIGH";

export type ActionLabel = "STRONG BUY" | "BUY" | "WATCH" | "AVOID";
export type ConvictionLevel = "HIGH" | "MEDIUM" | "LOW";

export interface TradeIntelligence {
  probability: number;          // 0-100
  qualityScore: number;         // 0-10
  riskLevel: RiskLevel;
  expectedMoveTime: string;     // "45 min"
  expectedOutcome: string;      // "TARGET LIKELY"
  action: ActionLabel;          // Decision engine output
  conviction: ConvictionLevel;  // Conviction level
  narrative?: string;
}

export interface Opportunity {
  id: string;
  symbol: string;
  direction: Direction;
  setup: SetupType;
  grade: SetupGrade;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  cmp: number | null;
  reasoning: string;
  status: WatchStatus;
  scores: {
    liquidity: boolean;
    structure: boolean;
    htf: boolean;
    entryQuality: "ok" | "warn" | "fail";
  };
  signals: {
    htfBias: string;
    orderBlock: string;
    fvg: string;
    sweep: string;
    structure: string;
  };
  sector: string | null;
  raw: ResearchDecisionCard;
  /** Phase 3 intelligence enrichment (present when from /api/trades or /ws/trades) */
  intelligence?: TradeIntelligence;
  /** Whether the user has marked this trade as taken */
  taken?: boolean;
}

const DEFAULT_SETUP: SetupType = "A";

function toNumber(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function normalizeSetup(raw: string | null | undefined): SetupType {
  if (!raw) return DEFAULT_SETUP;
  const s = raw.toString().toUpperCase();
  // Common patterns: SETUP_A, A, A+, smc-a etc.
  const m = s.match(/[A-D]/);
  return (m?.[0] as SetupType) ?? DEFAULT_SETUP;
}

export function gradeFromConfidence(score: number): SetupGrade {
  if (score >= 88) return "A+";
  if (score >= 75) return "A";
  if (score >= 60) return "B";
  return "C";
}

function pickReasoning(card: ResearchDecisionCard): string {
  const summary = card.reasoning_summary?.trim();
  if (summary) return summary;
  const reasoning = card.reasoning?.trim();
  if (reasoning) return reasoning;
  if (card.layer3_pass) return "All three SMC layers aligned — high quality structural setup.";
  if (card.layer2_pass) return "Multi-timeframe structure aligned, awaiting liquidity confirmation.";
  return "Setup is forming. Monitoring for trigger.";
}

function deriveDirection(card: ResearchDecisionCard): Direction {
  const entry = toNumber(card.entry_price);
  const stop = toNumber(card.stop_loss);
  if (entry != null && stop != null) {
    return stop < entry ? "BUY" : "SELL";
  }
  // Fallback: assume long bias
  return "BUY";
}

function deriveStatus(card: ResearchDecisionCard): WatchStatus {
  if (card.final_selected) return "Triggered";
  if (card.near_setup) return "Tapped";
  return "Waiting";
}

export function toOpportunity(card: ResearchDecisionCard): Opportunity {
  const direction = deriveDirection(card);
  const entry = toNumber(card.entry_price);
  const stop  = toNumber(card.stop_loss);
  const target = toNumber(card.target_1) ?? toNumber(card.target_2);
  let rr = toNumber(card.risk_reward);
  if (rr == null && entry != null && stop != null && target != null) {
    const risk = Math.abs(entry - stop);
    const reward = Math.abs(target - entry);
    rr = risk > 0 ? Number((reward / risk).toFixed(2)) : null;
  }
  const setup = normalizeSetup(card.setup);
  const grade = gradeFromConfidence(card.confidence_score ?? 0);
  const techSignals = card.technical_signals ?? {};

  const scores = {
    liquidity: Boolean(card.layer1_pass),
    structure: Boolean(card.layer2_pass),
    htf: Boolean(card.layer3_pass),
    entryQuality:
      grade === "A+" || grade === "A"
        ? ("ok" as const)
        : grade === "B"
        ? ("warn" as const)
        : ("fail" as const),
  };

  const signals = {
    htfBias: (techSignals["htf_bias"] as string | undefined) ?? (direction === "BUY" ? "Bullish" : "Bearish"),
    orderBlock: (techSignals["order_block"] as string | undefined) ?? (entry != null && stop != null ? `${stop.toFixed(2)} – ${entry.toFixed(2)}` : "Pending"),
    fvg: (techSignals["fvg"] as string | undefined) ?? (card.layer1_pass ? "Imbalance present" : "Not detected"),
    sweep: (techSignals["liquidity_sweep"] as string | undefined) ?? (card.layer1_pass ? "Confirmed" : "Awaiting"),
    structure: (techSignals["structure"] as string | undefined) ?? (card.layer2_pass ? "BOS confirmed" : "CHOCH watch"),
  };

  return {
    id: card.id != null ? `${card.symbol}-${card.id}` : `${card.symbol}-${card.setup ?? "x"}`,
    symbol: card.symbol,
    direction,
    setup,
    grade,
    entry,
    stop,
    target,
    rr,
    cmp: toNumber(card.scan_cmp),
    reasoning: pickReasoning(card),
    status: deriveStatus(card),
    scores,
    signals,
    sector: card.sector ?? null,
    raw: card,
  };
}

export function toOpportunities(cards: ResearchDecisionCard[] | undefined): Opportunity[] {
  if (!cards) return [];
  return cards.map(toOpportunity);
}

export function rrLabel(rr: number | null): string {
  if (rr == null) return "—";
  return `${rr.toFixed(2)}R`;
}

export function priceLabel(value: number | null): string {
  if (value == null) return "—";
  return value >= 1000 ? value.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : value.toFixed(2);
}

// ─────────────────────────────────────────────────────────────────────────
// Live-API adapter (Phase 2 — /api/trades + /ws/trades)
// ─────────────────────────────────────────────────────────────────────────

import type { LiveTrade } from "./useLiveTrades";

const LIVE_STATUS_MAP: Record<LiveTrade["status"], WatchStatus> = {
  WAITING: "Waiting",
  APPROACHING: "Approaching",
  TAPPED: "Tapped",
  TRIGGERED: "Triggered",
  RUNNING: "Running",
  TARGET_HIT: "TargetHit",
  STOP_HIT: "StopHit",
};

export function liveTradeToOpportunity(t: LiveTrade): Opportunity {
  const direction: Direction = t.direction === "LONG" ? "BUY" : "SELL";
  const setup = (t.setup ?? "A") as SetupType;
  const grade = (t.confidence ?? "B") as SetupGrade;
  const status = LIVE_STATUS_MAP[t.status] ?? "Waiting";
  return {
    id: t.id || `${t.symbol}-${t.timestamp ?? "live"}`,
    symbol: t.symbol,
    direction,
    setup,
    grade,
    entry: t.entry,
    stop: t.sl,
    target: t.target,
    rr: t.rr,
    cmp: t.entry,
    reasoning: t.analysis?.reason ?? "Live setup detected.",
    status,
    scores: {
      liquidity: Boolean(t.analysis?.liquidity),
      structure: Boolean(t.analysis?.structure),
      htf: Boolean(t.analysis?.htf_bias),
      entryQuality: grade === "A+" || grade === "A" ? "ok" : grade === "B" ? "warn" : "fail",
    },
    signals: {
      htfBias: t.analysis?.htf_bias ?? (direction === "BUY" ? "Bullish" : "Bearish"),
      orderBlock: t.analysis?.ob ? "Detected" : "Pending",
      fvg: t.analysis?.fvg ? "Imbalance present" : "Not detected",
      sweep: t.analysis?.liquidity ? "Confirmed" : "Awaiting",
      structure: t.analysis?.structure ?? "—",
    },
    sector: null,
    raw: {
      symbol: t.symbol,
      confidence_score: typeof t.score === "number" ? t.score : 0,
      setup: setup,
      entry_price: t.entry,
      stop_loss: t.sl,
      target_1: t.target,
      risk_reward: t.rr,
      reasoning: t.analysis?.reason,
      reasoning_summary: t.analysis?.reason,
      layer1_pass: Boolean(t.analysis?.liquidity),
      layer2_pass: Boolean(t.analysis?.structure),
      layer3_pass: Boolean(t.analysis?.htf_bias),
    } as unknown as ResearchDecisionCard,
    intelligence: t.intelligence
      ? {
          probability: t.intelligence.probability,
          qualityScore: t.intelligence.quality_score,
          riskLevel: t.intelligence.risk_level,
          expectedMoveTime: t.intelligence.expected_move_time,
          expectedOutcome: t.intelligence.expected_outcome,
          action: (t.intelligence.action ?? t.action ?? "WATCH") as ActionLabel,
          conviction: (t.intelligence.conviction ?? t.conviction ?? "MEDIUM") as ConvictionLevel,
          narrative: t.narrative,
        }
      : t.probability != null
        ? {
            probability: t.probability,
            qualityScore: t.quality_score ?? 0,
            riskLevel: (t.risk_level ?? "MED") as RiskLevel,
            expectedMoveTime: t.expected_move_time ?? "—",
            expectedOutcome: t.expected_outcome ?? "—",
            action: ((t.action) ?? "WATCH") as ActionLabel,
            conviction: ((t.conviction) ?? "MEDIUM") as ConvictionLevel,
            narrative: t.narrative,
          }
        : undefined,
  };
}

