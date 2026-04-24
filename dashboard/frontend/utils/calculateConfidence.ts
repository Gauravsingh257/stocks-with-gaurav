import type { StockAnalysis } from "@/lib/api";

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

/** True when the row is a primary SMC-backed setup (not empty / watchlist-tier filler). */
export function isPrimarySmcSetupType(setupType: string): boolean {
  if (!setupType || setupType === "No Valid SMC Setup") return false;
  if (/^WATCHLIST_/i.test(setupType)) return false;
  return true;
}

/**
 * Recommendation label aligned with backend `stock_search_analysis._recommendation`:
 * Strong Buy only if primary setup AND score ≥ 80; else Watchlist ≥ 50; else Avoid.
 */
export function recommendationLabelFromScanResult(analysis: StockAnalysis): RecommendationLabel {
  const setupOk = isPrimarySmcSetupType(analysis.setup_type);
  const c = analysis.confidence_score;
  if (setupOk && c >= 80) return "Strong Buy";
  if (c >= 50) return "Watchlist";
  return "Avoid";
}

/** Map API / card payload into pillar inputs for `calculateConfidence` (confluence view). */
export function stockAnalysisToConfidenceInputs(analysis: StockAnalysis): ConfidenceInputs {
  const zones = analysis.smc_zones ?? [];
  const zoneText = (z: (typeof zones)[0]) => `${z.type ?? ""} ${z.bottom ?? ""} ${z.top ?? ""} ${z.level ?? ""}`.toLowerCase();

  const hasOb = zones.some((z) => /order block/.test(zoneText(z)));
  const hasFvg = zones.some((z) => /fair value|fvg/.test(zoneText(z)));
  const hasStructure = zones.some((z) => /structure|bos|choch/.test(zoneText(z)));

  const primary = isPrimarySmcSetupType(analysis.setup_type);
  const trendStrength = Math.min(1, primary ? 0.58 + (hasStructure ? 0.22 : 0) + (hasOb ? 0.12 : 0) : 0.36);
  const volume = Math.min(1, 0.42 + Math.min(zones.length, 4) * 0.12 + (primary ? 0.08 : 0));

  const f = analysis.fundamentals;
  return {
    trendStrength,
    volume,
    smcSignals: {
      orderBlock: hasOb,
      liquiditySweep: hasFvg,
      bosConfirmation: hasStructure,
      fvg: hasFvg,
    },
    fundamentals: {
      score: f?.score,
      roePct: f?.roe_pct ?? null,
      debtEquity: f?.debt_equity ?? null,
      revenueGrowthPct: f?.revenue_growth_pct ?? null,
    },
    riskReward: analysis.risk_reward ?? null,
  };
}

/** API confidence score + confluence model from the same payload (for tooltips / parity checks). */
export function confidenceInsightsFromStockAnalysis(analysis: StockAnalysis): {
  apiScore: number;
  confluence: ReturnType<typeof calculateConfidence>;
  badgeLabel: RecommendationLabel;
} {
  const confluence = calculateConfidence(stockAnalysisToConfidenceInputs(analysis));
  return {
    apiScore: analysis.confidence_score,
    confluence,
    badgeLabel: recommendationLabelFromScanResult(analysis),
  };
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
