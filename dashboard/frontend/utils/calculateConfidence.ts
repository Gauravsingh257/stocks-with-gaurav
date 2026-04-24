export type RecommendationLabel = "Strong Buy" | "Watchlist" | "Avoid";

export interface ConfidenceInputs {
  trendStrength?: number;
  volume?: number;
  smcSignals?: {
    orderBlock?: boolean;
    liquiditySweep?: boolean;
    bosConfirmation?: boolean;
    fvg?: boolean;
  };
  fundamentals?: {
    score?: number;
    roePct?: number | null;
    debtEquity?: number | null;
    revenueGrowthPct?: number | null;
  };
  riskReward?: number | null;
}

export function calculateConfidence(input: ConfidenceInputs): {
  score: number;
  recommendation: RecommendationLabel;
} {
  let score = 20;

  score += Math.max(0, Math.min(input.trendStrength ?? 0, 1)) * 18;
  score += Math.max(0, Math.min(input.volume ?? 0, 1)) * 12;

  if (input.smcSignals?.orderBlock) score += 14;
  if (input.smcSignals?.liquiditySweep) score += 12;
  if (input.smcSignals?.bosConfirmation) score += 14;
  if (input.smcSignals?.fvg) score += 8;

  const rr = input.riskReward ?? 0;
  score += Math.min(Math.max(rr, 0), 4) * 4;

  const f = input.fundamentals;
  if (f?.score != null) score += Math.max(0, Math.min(f.score, 100)) * 0.1;
  if ((f?.roePct ?? 0) >= 15) score += 4;
  if ((f?.revenueGrowthPct ?? 0) >= 10) score += 3;
  if (f?.debtEquity != null && f.debtEquity <= 0.5) score += 3;

  const finalScore = Math.round(Math.max(0, Math.min(100, score)));
  const recommendation: RecommendationLabel =
    finalScore >= 75 ? "Strong Buy" : finalScore >= 50 ? "Watchlist" : "Avoid";

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
