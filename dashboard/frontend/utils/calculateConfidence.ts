export type RecommendationLabel = "Strong Buy" | "Watchlist" | "Avoid";

export interface ConfidenceInputs {
  /** 0–1 trend strength */
  trendStrength?: number;
  /** 0–1 volume / participation */
  volume?: number;
  smcSignals?: {
    orderBlock?: boolean;
    liquiditySweep?: boolean;
    bosConfirmation?: boolean;
    fvg?: boolean;
  };
  fundamentals?: {
    /** 0–100 style score when available */
    score?: number;
    roePct?: number | null;
    debtEquity?: number | null;
    revenueGrowthPct?: number | null;
  };
  riskReward?: number | null;
}

/**
 * Composite confidence: Trend 20%, Volume 20%, SMC 40%, Fundamentals 20% → 0–100.
 * Optional small R:R nudge (keeps pillars interpretable while reflecting payoff).
 */
export function calculateConfidence(input: ConfidenceInputs): {
  score: number;
  recommendation: RecommendationLabel;
} {
  const trend = Math.max(0, Math.min(input.trendStrength ?? 0, 1)) * 20;
  const vol = Math.max(0, Math.min(input.volume ?? 0, 1)) * 20;

  const s = input.smcSignals;
  let smcParts = 0;
  if (s?.orderBlock) smcParts += 1;
  if (s?.liquiditySweep) smcParts += 1;
  if (s?.bosConfirmation) smcParts += 1;
  if (s?.fvg) smcParts += 1;
  const smc = (smcParts / 4) * 40;

  const f = input.fundamentals;
  let fund = 10;
  if (f?.score != null) {
    fund = Math.max(0, Math.min(f.score, 100)) / 100 * 16;
    if ((f.roePct ?? 0) >= 15) fund += 2;
    if ((f.revenueGrowthPct ?? 0) >= 10) fund += 1;
    if (f.debtEquity != null && f.debtEquity <= 0.5) fund += 1;
  }
  fund = Math.min(20, fund);

  const rrNudge = Math.min(Math.max(input.riskReward ?? 0, 0), 4) * 1.25;
  const raw = trend + vol + smc + fund + rrNudge;
  const finalScore = Math.round(Math.max(0, Math.min(100, raw)));

  const recommendation: RecommendationLabel =
    finalScore >= 80 ? "Strong Buy" : finalScore >= 50 ? "Watchlist" : "Avoid";

  return { score: finalScore, recommendation };
}

export function recommendationColors(label: string): { bg: string; fg: string; border: string } {
  if (label === "Strong Buy") {
    return { bg: "rgba(0,224,150,0.14)", fg: "var(--success)", border: "rgba(0,224,150,0.3)" };
  }
  if (label === "Watchlist") {
    return { bg: "rgba(245,158,11,0.14)", fg: "var(--warning)", border: "rgba(245,158,11,0.3)" };
  }
  return { bg: "rgba(255,71,87,0.14)", fg: "var(--danger)", border: "rgba(255,71,87,0.3)" };
}

/** UI bucket: green high conviction (≥80), yellow medium (50–79), red low (&lt;50). */
export function confidenceVisualTier(score: number): "success" | "warning" | "danger" {
  if (score >= 80) return "success";
  if (score >= 50) return "warning";
  return "danger";
}
