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
export type WatchStatus = "Waiting" | "Tapped" | "Triggered";

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
  spark: number[];
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

function buildSpark(card: ResearchDecisionCard, direction: Direction): number[] {
  // Synthesize a deterministic preview line from key levels so cards always
  // show motion even before live tick streaming is wired in. The real chart
  // appears in the explanation drawer.
  const entry = toNumber(card.entry_price) ?? toNumber(card.scan_cmp) ?? 100;
  const cmp = toNumber(card.scan_cmp) ?? entry;
  const target = toNumber(card.target_1) ?? entry * (direction === "BUY" ? 1.05 : 0.95);
  const stop = toNumber(card.stop_loss) ?? entry * (direction === "BUY" ? 0.97 : 1.03);
  const start = direction === "BUY" ? Math.min(stop, cmp * 0.985) : Math.max(stop, cmp * 1.015);
  const end = cmp;
  const peak = direction === "BUY" ? Math.min(target, end * 1.01) : Math.max(target, end * 0.99);
  const seed = (card.symbol?.charCodeAt(0) ?? 65) % 7;
  const points: number[] = [];
  for (let i = 0; i < 24; i++) {
    const t = i / 23;
    const wobble = Math.sin(t * Math.PI * (1.6 + seed * 0.1)) * (Math.abs(end - start) * 0.18);
    const base = start + (end - start) * t;
    const climb = (peak - end) * Math.pow(t, 2) * 0.25;
    points.push(base + wobble + climb);
  }
  return points;
}

export function toOpportunity(card: ResearchDecisionCard): Opportunity {
  const direction = deriveDirection(card);
  const entry = toNumber(card.entry_price);
  const stop = toNumber(card.stop_loss);
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
    spark: buildSpark(card, direction),
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
