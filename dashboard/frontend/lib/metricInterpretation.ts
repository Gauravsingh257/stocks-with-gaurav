/**
 * Plain-English readings for the key metrics on /stock/<symbol>.
 *
 * The page used to print bare numbers ("P/E 222x", "D/E 0.01x"), leaving a
 * visitor to know whether that was good. Each reader here turns one value into
 * a tone (good / caution / weak / neutral / unavailable), a short label and a
 * one-sentence note. The values themselves are not recalculated.
 *
 * Why not one threshold per metric:
 *   - Valuation and margins are sector-relative. On the 2026-09-12 universe the
 *     median P/E is 38.9x in Pharma but 17.9x in Finance; the median net margin
 *     is 27.8% in Finance but 4.6% in FMCG. P/E, P/B and net margin are therefore
 *     compared with the stock's sector median, falling back to the NSE-wide
 *     median when the sector has too few reporting stocks.
 *   - Debt-to-equity is not a health check for lenders, whose business is
 *     borrowing (Finance p75 is 2.9x), and capital-heavy sectors run more debt
 *     (Power median 1.5x against 0.3x overall), so those get their own ranges.
 *   - ROE, revenue growth, promoter holding and price performance use fixed
 *     ranges: their meaning does not change much between sectors.
 *
 * Positioning: these are descriptive labels for education, not ratings or
 * recommendations. Nothing here may say buy, sell or hold.
 */

export type Tone = "good" | "caution" | "weak" | "neutral" | "unavailable";

export interface Reading {
  tone: Tone;
  /** A few words for the status pill, e.g. "Very high". */
  label: string;
  /** One sentence on what the number means for this company. */
  note: string;
}

export interface SectorBenchmarks {
  /** The sector the medians describe; null when only market-wide medians apply. */
  sector: string | null;
  /** Stocks in the sector sample. */
  count: number;
  pe: number | null;
  pb: number | null;
  netMargin: number | null;
}

/** Medians across the 2,348 NSE equities in the 2026-09-12 universe snapshot. */
export const MARKET_MEDIANS = { pe: 25.5, pb: 2.4, netMargin: 6.7 } as const;

/** A sector median needs at least this many reporting stocks to be used. */
export const MIN_SECTOR_SAMPLE = 8;

const PLACEHOLDER_SECTORS = new Set(["", "UNKNOWN", "UNASSIGNED", "OTHER", "OTHERS"]);
const LENDER_SECTORS = new Set(["FINANCE"]);
const CAPITAL_INTENSIVE_SECTORS = new Set(["POWER", "UTILITIES", "INFRA", "REALTY", "TELECOM"]);

function isNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** "7.5", "174.8", "125" — one decimal at most, matching the card values. */
function fmt(value: number): string {
  return Number(value.toFixed(1)).toLocaleString("en-IN");
}

function unavailable(note: string): Reading {
  return { tone: "unavailable", label: "Not available", note };
}

/** The sector name, or null for placeholders such as "Unknown". */
export function realSector(sector: string | null | undefined): string | null {
  const name = (sector ?? "").trim();
  return PLACEHOLDER_SECTORS.has(name.toUpperCase()) ? null : name;
}

export function median(values: ReadonlyArray<number | null | undefined>): number | null {
  const xs = values.filter(isNum).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

type BenchmarkRow = { pe?: number | null; pb?: number | null; net_margin_pct?: number | null };

function sampleMedian(values: number[]): number | null {
  return values.length >= MIN_SECTOR_SAMPLE ? median(values) : null;
}

export function sectorBenchmarks(
  sector: string | null | undefined,
  rows: ReadonlyArray<BenchmarkRow>,
): SectorBenchmarks {
  const name = realSector(sector);
  if (!name) return { sector: null, count: 0, pe: null, pb: null, netMargin: null };
  const positive = (values: Array<number | null | undefined>) =>
    values.filter((v): v is number => isNum(v) && v > 0);
  return {
    sector: name,
    count: rows.length,
    // A missing or negative P/E or P/B is a loss-maker or negative book, not a cheap stock.
    pe: sampleMedian(positive(rows.map((r) => r.pe))),
    pb: sampleMedian(positive(rows.map((r) => r.pb))),
    netMargin: sampleMedian(rows.map((r) => r.net_margin_pct).filter(isNum)),
  };
}

interface Basis {
  value: number;
  /** "sector" or "market", for labels such as "Above sector". */
  peers: "sector" | "market";
  /** "the Chemicals median (22.7x)". */
  phrase: string;
}

function basis(bench: SectorBenchmarks | null | undefined, key: "pe" | "pb" | "netMargin", unit: string): Basis {
  const sectorValue = bench?.[key];
  if (bench?.sector && isNum(sectorValue) && sectorValue > 0) {
    return { value: sectorValue, peers: "sector", phrase: `the ${bench.sector} median (${fmt(sectorValue)}${unit})` };
  }
  const value = MARKET_MEDIANS[key];
  return { value, peers: "market", phrase: `the NSE median (${fmt(value)}${unit})` };
}

const about = (ratio: number) => `About ${ratio.toFixed(1)}×`;

export function readPE(
  pe: number | null | undefined,
  netMarginPct: number | null | undefined,
  bench?: SectorBenchmarks | null,
): Reading {
  if (!isNum(pe) || pe <= 0) {
    if ((isNum(pe) && pe <= 0) || (isNum(netMarginPct) && netMarginPct < 0)) {
      return { tone: "weak", label: "Loss-making", note: "No meaningful P/E: the company made a loss over the last year." };
    }
    return unavailable("P/E isn't available for this stock.");
  }
  const b = basis(bench, "pe", "x");
  const ratio = pe / b.value;
  if (pe >= 100 || ratio >= 2) {
    return { tone: "weak", label: "Very high", note: `${about(ratio)} ${b.phrase}. Investors are paying a lot for each rupee of profit.` };
  }
  if (ratio >= 1.3) {
    return { tone: "caution", label: `Above ${b.peers}`, note: `${about(ratio)} ${b.phrase}, a premium valuation.` };
  }
  if (ratio >= 0.7) return { tone: "good", label: "In line", note: `Close to ${b.phrase}.` };
  return {
    tone: "good",
    label: `Below ${b.peers}`,
    note: `Cheaper than ${b.phrase}. A low P/E can also reflect doubts about future profits.`,
  };
}

export function readPB(
  pb: number | null | undefined,
  roePct: number | null | undefined,
  bench?: SectorBenchmarks | null,
): Reading {
  if (!isNum(pb)) return unavailable("Price-to-book isn't available for this stock.");
  if (pb <= 0) {
    return { tone: "weak", label: "Negative book", note: "Liabilities exceed assets on the balance sheet." };
  }
  const b = basis(bench, "pb", "x");
  const ratio = pb / b.value;
  const highRoe = isNum(roePct) && roePct >= 20;
  const roeNote = highRoe ? ` A high ROE (${fmt(roePct)}%) can justify paying well above book value.` : "";
  if (ratio >= 3) {
    return { tone: highRoe ? "caution" : "weak", label: "Very high", note: `${about(ratio)} ${b.phrase}.${roeNote}` };
  }
  if (ratio >= 1.5) {
    return { tone: highRoe ? "good" : "caution", label: `Above ${b.peers}`, note: `${about(ratio)} ${b.phrase}.${roeNote}` };
  }
  if (ratio >= 0.6) return { tone: "good", label: "In line", note: `Close to ${b.phrase}.` };
  return {
    tone: "good",
    label: `Below ${b.peers}`,
    note: pb < 1 ? `Trades below its book value, under ${b.phrase}.` : `Cheaper than ${b.phrase} relative to book value.`,
  };
}

export function readROE(roePct: number | null | undefined): Reading {
  if (!isNum(roePct)) return unavailable("Return on equity isn't reported for this stock yet.");
  if (roePct < 0) return { tone: "weak", label: "Negative", note: "Lost money on shareholders' equity over the last year." };
  const earns = `Earns about ₹${fmt(roePct)} a year for every ₹100 of shareholder equity`;
  if (roePct < 10) return { tone: "weak", label: "Weak", note: `${earns}, below the 12–15% investors usually expect.` };
  if (roePct < 15) return { tone: "caution", label: "Moderate", note: `${earns}, around the 12–15% investors usually expect.` };
  if (roePct < 20) return { tone: "good", label: "Good", note: `${earns}.` };
  return { tone: "good", label: "Excellent", note: `${earns}, a highly capital-efficient business.` };
}

export function readDebtToEquity(
  debtToEquity: number | null | undefined,
  sector: string | null | undefined,
): Reading {
  const name = realSector(sector);
  const key = (name ?? "").toUpperCase();
  if (LENDER_SECTORS.has(key)) {
    return {
      tone: "neutral",
      label: "Not comparable",
      note: "Lenders borrow in order to lend, so debt-to-equity isn't a useful health check for financial companies.",
    };
  }
  if (!isNum(debtToEquity)) return unavailable("Debt-to-equity isn't available for this stock.");
  const perHundred = `₹${fmt(debtToEquity * 100)} of debt for every ₹100 of equity.`;
  if (CAPITAL_INTENSIVE_SECTORS.has(key)) {
    if (debtToEquity <= 1) {
      return {
        tone: "good",
        label: debtToEquity < 0.1 ? "Very low" : "Low for sector",
        note: `${perHundred} ${name} businesses often carry more debt than this.`,
      };
    }
    if (debtToEquity <= 2) {
      return { tone: "caution", label: "Moderate", note: `${perHundred} Common in capital-heavy ${name}, but worth watching.` };
    }
    return { tone: "weak", label: "High", note: `${perHundred} Heavy borrowing even for a capital-heavy sector.` };
  }
  if (debtToEquity < 0.1) return { tone: "good", label: "Very low · Healthy", note: "Almost no debt relative to shareholder equity." };
  if (debtToEquity <= 0.5) return { tone: "good", label: "Low · Healthy", note: `${perHundred} Comfortable borrowing.` };
  if (debtToEquity <= 1) return { tone: "caution", label: "Moderate", note: `${perHundred} Manageable, but worth watching.` };
  if (debtToEquity <= 2) return { tone: "weak", label: "High", note: `${perHundred} Borrowing is heavy relative to equity.` };
  return { tone: "weak", label: "Very high", note: `${perHundred} Debt is more than twice shareholder equity.` };
}

export function readRevenueGrowth(growthPct: number | null | undefined): Reading {
  if (!isNum(growthPct)) return unavailable("Revenue growth isn't available for this stock.");
  const period = "latest quarter vs a year earlier";
  if (growthPct < 0) return { tone: "weak", label: "Declining", note: `Revenue fell ${fmt(Math.abs(growthPct))}% (${period}).` };
  if (growthPct < 10) return { tone: "caution", label: "Slow", note: `Revenue grew ${fmt(growthPct)}% (${period}), barely ahead of inflation.` };
  if (growthPct < 25) return { tone: "good", label: "Healthy", note: `Revenue grew ${fmt(growthPct)}% (${period}).` };
  if (growthPct <= 100) return { tone: "good", label: "Strong", note: `Revenue grew ${fmt(growthPct)}% (${period}).` };
  return {
    tone: "caution",
    label: "Unusually high",
    note: `Revenue grew ${fmt(growthPct)}% (${period}). Jumps this large often come from a low base, an acquisition or a one-off.`,
  };
}

export function readNetMargin(
  netMarginPct: number | null | undefined,
  bench?: SectorBenchmarks | null,
): Reading {
  if (!isNum(netMarginPct)) return unavailable("Net margin isn't available for this stock.");
  if (netMarginPct < 0) {
    return { tone: "weak", label: "Loss-making", note: `Lost about ₹${fmt(Math.abs(netMarginPct))} for every ₹100 of revenue.` };
  }
  const b = basis(bench, "netMargin", "%");
  const keeps = `Keeps about ₹${fmt(netMarginPct)} of profit from every ₹100 of revenue`;
  if (netMarginPct >= Math.max(b.value * 1.5, b.value + 5)) {
    return { tone: "good", label: `Above ${b.peers}`, note: `${keeps}, well above ${b.phrase}.` };
  }
  if (netMarginPct < 2) return { tone: "caution", label: "Very thin", note: `${keeps}, leaving little room for error.` };
  if (netMarginPct >= b.value * 0.8) return { tone: "good", label: "In line", note: `${keeps}, close to ${b.phrase}.` };
  if (netMarginPct >= b.value * 0.4) return { tone: "caution", label: `Below ${b.peers}`, note: `${keeps}, below ${b.phrase}.` };
  return { tone: "weak", label: `Well below ${b.peers}`, note: `${keeps}, well below ${b.phrase}.` };
}

export function readPromoterHolding(promoterPct: number | null | undefined): Reading {
  if (!isNum(promoterPct)) return unavailable("Promoter holding isn't available for this stock.");
  const holds = `Promoters and insiders own ${fmt(promoterPct)}%`;
  if (promoterPct >= 50) return { tone: "good", label: "High", note: `${holds}, a strong alignment with shareholders.` };
  if (promoterPct >= 35) return { tone: "good", label: "Moderate", note: `${holds}.` };
  if (promoterPct >= 20) return { tone: "caution", label: "Low", note: `${holds}. Worth checking for recent stake sales or pledging.` };
  return {
    tone: "neutral",
    label: "Widely held",
    note: `${holds}. Common for professionally run companies such as many banks; not a weakness by itself.`,
  };
}

/** `pctBelowHigh` is how far the price sits below its 52-week high, 0 or more. */
export function read52WeekHigh(pctBelowHigh: number | null | undefined): Reading {
  if (!isNum(pctBelowHigh)) return unavailable("The 52-week range isn't available for this stock.");
  const below = `${fmt(pctBelowHigh)}% below its highest price of the past year`;
  if (pctBelowHigh <= 5) return { tone: "good", label: "Near 52-week high", note: `Trading ${below}.` };
  if (pctBelowHigh <= 15) return { tone: "good", label: "Close to high", note: `Trading ${below}.` };
  if (pctBelowHigh <= 30) return { tone: "caution", label: "Pullback", note: `Trading ${below}.` };
  if (pctBelowHigh <= 50) return { tone: "weak", label: "Well below high", note: `Trading ${below}.` };
  return { tone: "weak", label: "Deep fall", note: `Trading ${below}, having lost half its value or more.` };
}

export function readOneYearReturn(returnPct: number | null | undefined): Reading {
  if (!isNum(returnPct)) return unavailable("A one-year price history isn't available for this stock.");
  if (returnPct >= 25) return { tone: "good", label: "Strong", note: `Up ${fmt(returnPct)}% over the past year.` };
  if (returnPct >= 0) return { tone: "good", label: "Positive", note: `Up ${fmt(returnPct)}% over the past year.` };
  if (returnPct >= -15) return { tone: "caution", label: "Negative", note: `Down ${fmt(Math.abs(returnPct))}% over the past year.` };
  return { tone: "weak", label: "Sharp fall", note: `Down ${fmt(Math.abs(returnPct))}% over the past year.` };
}

/** Size band only — size is context, not quality, so the tone is always neutral. */
export function readMarketCap(marketCapCr: number | null | undefined): Reading {
  if (!isNum(marketCapCr)) return unavailable("Market capitalisation isn't available for this stock.");
  if (marketCapCr >= 100000) {
    return { tone: "neutral", label: "Large cap", note: "Among India's biggest listed companies (roughly ₹1 lakh Cr and above)." };
  }
  if (marketCapCr >= 30000) return { tone: "neutral", label: "Mid cap", note: "A mid-sized listed company (roughly ₹30,000 Cr to ₹1 lakh Cr)." };
  if (marketCapCr >= 5000) return { tone: "neutral", label: "Small cap", note: "A smaller company (roughly ₹5,000–30,000 Cr); prices tend to swing more." };
  return { tone: "neutral", label: "Micro cap", note: "A very small company; prices can swing sharply and trading can be thin." };
}
