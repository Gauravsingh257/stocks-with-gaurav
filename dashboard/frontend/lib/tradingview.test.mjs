// Unit tests for lib/tradingview.ts.
// Run: node --test dashboard/frontend/lib/tradingview.test.mjs
//
// Expected symbols were checked against TradingView's symbol search on
// 2026-09-15. A bare "FCL" resolves to ASX:FCL (FINEOS), not Fineotex Chemical.
import { test } from "node:test";
import assert from "node:assert/strict";
import { tradingViewChartUrl, tradingViewSymbol, tradingViewTicker } from "./tradingview.ts";

test("always carries the NSE exchange, so ambiguous tickers cannot resolve elsewhere", () => {
  for (const sym of ["FCL", "ABB", "ITC", "IDEA", "SBIN", "TCS"]) {
    assert.equal(tradingViewSymbol(sym), `NSE:${sym}`);
  }
});

test("hyphens become underscores, ampersands are kept", () => {
  assert.equal(tradingViewSymbol("BAJAJ-AUTO"), "NSE:BAJAJ_AUTO");
  assert.equal(tradingViewSymbol("NAM-INDIA"), "NSE:NAM_INDIA");
  assert.equal(tradingViewSymbol("KLBRENG-B"), "NSE:KLBRENG_B");
  assert.equal(tradingViewSymbol("M&M"), "NSE:M&M");
  assert.equal(tradingViewSymbol("J&KBANK"), "NSE:J&KBANK");
  assert.equal(tradingViewSymbol("360ONE"), "NSE:360ONE");
});

test("strips existing decoration and normalises case", () => {
  assert.equal(tradingViewTicker(" nse:m&m "), "M&M");
  assert.equal(tradingViewTicker("SBIN.NS"), "SBIN");
  assert.equal(tradingViewSymbol("NSE:FCL"), "NSE:FCL");
});

test("chart URLs encode the whole symbol, including & and :", () => {
  assert.equal(
    tradingViewChartUrl("M&M", "D"),
    "https://www.tradingview.com/chart/?symbol=NSE%3AM%26M&interval=D",
  );
  assert.equal(tradingViewChartUrl("BAJAJ-AUTO"), "https://www.tradingview.com/chart/?symbol=NSE%3ABAJAJ_AUTO");
});
