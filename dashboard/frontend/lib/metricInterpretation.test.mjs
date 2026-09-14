// Unit tests for the stock-page metric readings (lib/metricInterpretation.ts).
// Run: node --test dashboard/frontend/lib/metricInterpretation.test.mjs
//
// Real rows from the 2026-09-12 universe snapshot anchor the cases: FCL
// (P/E 222x, D/E 0.01x, revenue +174.8%), TCS (P/B 7.26x with ROE 47.7%) and
// HDFCBANK (no D/E, promoter 0.15%). Sector medians match that snapshot.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MARKET_MEDIANS,
  MIN_SECTOR_SAMPLE,
  median,
  read52WeekHigh,
  readDebtToEquity,
  readMarketCap,
  readNetMargin,
  readOneYearReturn,
  readPB,
  readPE,
  readPromoterHolding,
  readROE,
  readRevenueGrowth,
  realSector,
  sectorBenchmarks,
} from "./metricInterpretation.ts";

const bench = (sector, pe, pb, netMargin) => ({ sector, count: 200, pe, pb, netMargin });
const CHEMICALS = bench("Chemicals", 22.7, 1.9, 5.5);
const IT = bench("IT", 23.1, 3.0, 8.8);
const FINANCE = bench("Finance", 17.9, 1.6, 27.8);

test("FCL: a 222x P/E is very high against the Chemicals median", () => {
  const r = readPE(222, 12.09, CHEMICALS);
  assert.equal(r.tone, "weak");
  assert.equal(r.label, "Very high");
  assert.match(r.note, /Chemicals median \(22\.7x\)/);
});

test("P/E is relative to the sector, not one universal threshold", () => {
  // 40x is a premium in Chemicals (1.8× the median) but in line for Pharma (median 38.9x).
  assert.equal(readPE(40, 10, CHEMICALS).tone, "caution");
  assert.equal(readPE(40, 10, bench("Pharma", 38.9, 4.6, 11)).label, "In line");
  assert.equal(readPE(16.0, 18, IT).label, "Below sector");
  assert.equal(readPE(15.2, 26.8, FINANCE).label, "In line");
  assert.equal(readPE(26, 10, CHEMICALS).label, "In line");
  assert.equal(readPE(32, 10, CHEMICALS).label, "Above sector");
});

test("a missing P/E is loss-making when margins are negative, otherwise unavailable", () => {
  assert.deepEqual([readPE(null, -4).tone, readPE(null, -4).label], ["weak", "Loss-making"]);
  assert.equal(readPE(-12, 3).label, "Loss-making");
  assert.equal(readPE(null, 5).tone, "unavailable");
  assert.equal(readPE(undefined, null).tone, "unavailable");
});

test("falls back to the NSE median when the sector sample is too small", () => {
  const rows = Array.from({ length: MIN_SECTOR_SAMPLE - 1 }, () => ({ pe: 30, pb: 2, net_margin_pct: 7 }));
  const small = sectorBenchmarks("Cement", rows);
  assert.equal(small.pe, null);
  const r = readPE(40, 7, small);
  assert.match(r.note, /NSE median \(25\.5x\)/);
  assert.equal(r.label, "Above market");
});

test("sector benchmarks ignore placeholders and non-positive valuations", () => {
  assert.equal(sectorBenchmarks("Unknown", [{ pe: 10 }]).sector, null);
  assert.equal(realSector(" Unassigned "), null);
  assert.equal(realSector("IT"), "IT");
  const rows = [
    ...Array.from({ length: 9 }, (_, i) => ({ pe: 10 + i, pb: 1 + i, net_margin_pct: i })),
    { pe: -5, pb: -2, net_margin_pct: null },
    { pe: null, pb: null, net_margin_pct: Number.NaN },
  ];
  const b = sectorBenchmarks("Metal", rows);
  assert.equal(b.count, 11);
  assert.equal(b.pe, 14);
  assert.equal(b.pb, 5);
  assert.equal(b.netMargin, 4);
});

test("median handles odd, even, empty and non-finite input", () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 3, 2]), 2.5);
  assert.equal(median([]), null);
  assert.equal(median([null, Number.NaN, undefined, 5]), 5);
});

test("FCL: D/E 0.01x reads as very low and healthy", () => {
  const r = readDebtToEquity(0.01, "Chemicals");
  assert.equal(r.tone, "good");
  assert.equal(r.label, "Very low · Healthy");
});

test("D/E is not a health check for lenders, and capital-heavy sectors get wider ranges", () => {
  assert.equal(readDebtToEquity(null, "Finance").tone, "neutral");
  assert.equal(readDebtToEquity(4.2, "Finance").label, "Not comparable");
  assert.equal(readDebtToEquity(1.5, "Power").tone, "caution");
  assert.equal(readDebtToEquity(0.8, "Power").label, "Low for sector");
  assert.equal(readDebtToEquity(1.5, "Chemicals").label, "High");
  assert.equal(readDebtToEquity(0.3, "Unknown").label, "Low · Healthy");
  assert.equal(readDebtToEquity(0.7, "Auto").tone, "caution");
  assert.equal(readDebtToEquity(3, "Auto").label, "Very high");
  assert.equal(readDebtToEquity(null, "Auto").tone, "unavailable");
});

test("P/B: a high ROE softens a premium to book", () => {
  assert.deepEqual([readPB(7.26, 47.74, IT).tone, readPB(7.26, 47.74, IT).label], ["good", "Above sector"]);
  assert.equal(readPB(7.26, null, IT).tone, "caution");
  assert.equal(readPB(12, 8, IT).tone, "weak");
  assert.equal(readPB(12, 30, IT).tone, "caution");
  assert.equal(readPB(5.44, null, CHEMICALS).label, "Above sector");
  assert.equal(readPB(-1, null, IT).label, "Negative book");
  assert.match(readPB(0.8, null, FINANCE).note, /below its book value/);
});

test("ROE uses fixed ranges around a ~12-15% cost of equity", () => {
  assert.equal(readROE(null).tone, "unavailable");
  assert.equal(readROE(47.74).label, "Excellent");
  assert.equal(readROE(17).label, "Good");
  assert.equal(readROE(13.84).tone, "caution");
  assert.equal(readROE(6).tone, "weak");
  assert.equal(readROE(-3).label, "Negative");
});

test("revenue growth: very large jumps are flagged, not celebrated", () => {
  assert.deepEqual([readRevenueGrowth(174.8).tone, readRevenueGrowth(174.8).label], ["caution", "Unusually high"]);
  assert.equal(readRevenueGrowth(64).label, "Strong");
  assert.equal(readRevenueGrowth(16.6).label, "Healthy");
  assert.equal(readRevenueGrowth(4).tone, "caution");
  assert.equal(readRevenueGrowth(-4).label, "Declining");
});

test("net margin is compared with the sector", () => {
  assert.equal(readNetMargin(26.79, FINANCE).label, "In line");
  assert.equal(readNetMargin(12.09, CHEMICALS).label, "Above sector");
  assert.equal(readNetMargin(6, FINANCE).label, "Well below sector");
  assert.equal(readNetMargin(14, FINANCE).tone, "caution");
  assert.equal(readNetMargin(1.2, bench("FMCG", 20, 2, 1.0)).label, "Very thin");
  assert.equal(readNetMargin(-3, CHEMICALS).label, "Loss-making");
  assert.match(readNetMargin(7, null).note, new RegExp(`NSE median \\(${MARKET_MEDIANS.netMargin}%\\)`));
});

test("a small promoter stake reads as widely held, not weak", () => {
  assert.deepEqual([readPromoterHolding(0.15).tone, readPromoterHolding(0.15).label], ["neutral", "Widely held"]);
  assert.equal(readPromoterHolding(67.25).label, "High");
  assert.equal(readPromoterHolding(40).label, "Moderate");
  assert.equal(readPromoterHolding(25.21).tone, "caution");
});

test("price performance readings", () => {
  assert.equal(read52WeekHigh(3).label, "Near 52-week high");
  assert.equal(read52WeekHigh(7.5).label, "Close to high");
  assert.equal(read52WeekHigh(21.8).tone, "caution");
  assert.equal(read52WeekHigh(60).label, "Deep fall");
  assert.equal(readOneYearReturn(140.52).label, "Strong");
  assert.equal(readOneYearReturn(4).label, "Positive");
  assert.equal(readOneYearReturn(-8.2).tone, "caution");
  assert.equal(readOneYearReturn(-27.6).label, "Sharp fall");
});

test("market cap is a neutral size band", () => {
  assert.equal(readMarketCap(1091700.7).label, "Large cap");
  assert.equal(readMarketCap(45000).label, "Mid cap");
  assert.equal(readMarketCap(6721.5).label, "Small cap");
  assert.equal(readMarketCap(900).label, "Micro cap");
  for (const v of [1091700, 45000, 6721, 900]) assert.equal(readMarketCap(v).tone, "neutral");
});

test("every reader treats null, undefined and NaN as unavailable", () => {
  const readers = [
    (v) => readPE(v, null, CHEMICALS),
    (v) => readPB(v, null, CHEMICALS),
    readROE,
    (v) => readDebtToEquity(v, "Chemicals"),
    readRevenueGrowth,
    (v) => readNetMargin(v, CHEMICALS),
    readPromoterHolding,
    read52WeekHigh,
    readOneYearReturn,
    readMarketCap,
  ];
  for (const read of readers) {
    for (const v of [null, undefined, Number.NaN]) {
      const r = read(v);
      assert.equal(r.tone, "unavailable");
      assert.equal(r.label, "Not available");
      assert.ok(r.note.length > 0);
    }
  }
});

test("no reading uses advice language", () => {
  const samples = [
    readPE(222, 12, CHEMICALS), readPE(10, 12, CHEMICALS), readPB(0.5, 5, FINANCE),
    readROE(30), readDebtToEquity(3, "Auto"), readRevenueGrowth(-20), readNetMargin(40, FINANCE),
    readPromoterHolding(10), read52WeekHigh(80), readOneYearReturn(-60), readMarketCap(200000),
  ];
  for (const r of samples) {
    assert.doesNotMatch(`${r.label} ${r.note}`, /\b(buy|sell|hold|recommend|undervalued|overvalued)\b/i);
  }
});
