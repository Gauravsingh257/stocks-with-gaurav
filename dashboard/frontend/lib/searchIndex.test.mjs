// Unit tests for the global search core (lib/searchIndex.ts).
// Run: node --test dashboard/frontend/lib/searchIndex.test.mjs
// Node >= 22.18 strips TypeScript types natively, so this imports the real module.
//
// The cases come from the 2026-09-14 audit of real searches: 11 of 24 stock
// searches opened a 404 (RELIANCCE, ICICI, DATA, PINELAB), and page names such as
// "watchlist" put a fake stock above the real page.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildSearchPages,
  buildStockIndex,
  editDistance,
  flattenGroups,
  normalizeQuery,
  searchAll,
  typoBudget,
} from "./searchIndex.ts";

// Turnover-sorted, like /api/research/universe/sitemap.
const ROWS = [
  { symbol: "RELIANCE", company_name: "Reliance Industries Limited", sector: "Energy" },
  { symbol: "HDFCBANK", company_name: "HDFC Bank Limited", sector: "Financial Services" },
  { symbol: "ICICIBANK", company_name: "ICICI Bank Limited", sector: "Financial Services" },
  { symbol: "TMPV", company_name: "Tata Motors Passenger Vehicles Limited", sector: "Automobile and Auto Components" },
  { symbol: "SUNPHARMA", company_name: "Sun Pharmaceutical Industries Limited", sector: "Healthcare" },
  { symbol: "HDFCAMC", company_name: "HDFC Asset Management Company Limited", sector: "Financial Services" },
  { symbol: "M&M", company_name: "Mahindra & Mahindra Limited", sector: "Automobile and Auto Components" },
  { symbol: "TMCV", company_name: "Tata Motors Limited", sector: "Automobile and Auto Components" },
  { symbol: "PINELABS", company_name: "Pine Labs Limited", sector: "Financial Services" },
  { symbol: "DATAPATTNS", company_name: "Data Patterns (India) Limited", sector: "Capital Goods" },
  { symbol: "RELAXO", company_name: "Relaxo Footwears Limited", sector: "Consumer Durables" },
  { symbol: "UNASSIGNEDCO", company_name: "Placeholder Sector Limited", sector: "Unassigned" },
  { symbol: "SBIN", company_name: "State Bank of India", sector: "Finance" },
  { symbol: "NITINSPIN", company_name: "Nitin Spinners Limited", sector: "Textiles" },
  { symbol: "RELIANCE", company_name: "duplicate row must be ignored", sector: "Energy" },
];

const STOCKS = buildStockIndex(ROWS);
const PAGES = buildSearchPages("/intelligence");
const KNOWN = new Set(STOCKS.map((s) => s.symbol));
const run = (q, stocks = STOCKS) => flattenGroups(searchAll(q, stocks, PAGES));
const stocksFor = (q) => run(q).filter((r) => r.kind === "stock");
const symbolsFor = (q) => stocksFor(q).map((r) => r.label);

test("typo matches are a fallback — anything matching as typed hides them", () => {
  // Found live on 2026-09-14: "sbin" also listed Nitin Spinners (SPIN is one
  // edit from SBIN) and the AI Research page (its keyword "swing").
  assert.deepEqual(symbolsFor("sbin"), ["SBIN"]);
  assert.equal(run("sbin").some((r) => r.fuzzy), false);
  assert.equal(run("sbin").some((r) => r.kind === "page"), false);
  // With nothing matching as typed, typo tolerance still does its job.
  assert.equal(stocksFor("RELIANCCE")[0].label, "RELIANCE");
  assert.equal(stocksFor("RELIANCCE")[0].fuzzy, true);
});

test("the index drops duplicates and keeps turnover order as rank", () => {
  assert.equal(STOCKS.filter((s) => s.symbol === "RELIANCE").length, 1);
  assert.equal(STOCKS[0].symbol, "RELIANCE");
  assert.equal(STOCKS[0].rank, 0);
});

test("an exact symbol ranks first", () => {
  assert.equal(symbolsFor("reliance")[0], "RELIANCE");
  assert.equal(run("RELIANCE")[0].href, "/stock/RELIANCE");
});

test("a company name finds symbols the user does not know", () => {
  const hits = symbolsFor("tata motors");
  assert.ok(hits.includes("TMCV") && hits.includes("TMPV"), hits.join(","));
});

test("a half-typed last word still matches", () => {
  assert.ok(symbolsFor("tata m").includes("TMCV"));
  assert.ok(symbolsFor("sun pharma").includes("SUNPHARMA"));
});

test("typos are tolerated and flagged as fuzzy", () => {
  const typo = stocksFor("RELIANCCE");
  assert.equal(typo[0].label, "RELIANCE");
  assert.equal(typo[0].fuzzy, true);
  const transposed = stocksFor("HDFCBNAK");
  assert.equal(transposed[0].label, "HDFCBANK");
  assert.equal(transposed[0].fuzzy, true);
  assert.ok(symbolsFor("pharmacutical").includes("SUNPHARMA"));
});

test("the audit's 404 queries now reach a real stock", () => {
  assert.equal(symbolsFor("icici")[0], "ICICIBANK");
  assert.equal(symbolsFor("pinelab")[0], "PINELABS");
  assert.ok(symbolsFor("data").includes("DATAPATTNS"));
});

test("page names never produce a fake stock, and the page leads", () => {
  for (const q of ["watchlist", "terminal", "research", "screeners", "portfolio", "journal"]) {
    const results = run(q);
    assert.equal(results.filter((r) => r.kind === "stock" && !KNOWN.has(r.label)).length, 0, q);
    assert.equal(results[0].kind, "page", q);
  }
  assert.equal(run("watchlist")[0].href, "/watchlist");
});

test("every stock result points at a symbol that exists in the universe", () => {
  for (const q of ["r", "re", "rel", "hdfc", "bank", "tata", "icic", "zz", "pine labs", "m&m", "limited"]) {
    for (const r of stocksFor(q)) {
      assert.ok(KNOWN.has(r.label), `${q} -> ${r.label}`);
      assert.equal(r.href, `/stock/${encodeURIComponent(r.label)}`);
    }
  }
});

test("gibberish and noise words return no stocks", () => {
  assert.deepEqual(symbolsFor("ZZQXJK"), []);
  assert.deepEqual(symbolsFor("limited"), []);
});

test("without a loaded index, only pages are searched — no stock is guessed", () => {
  const results = run("reliance", null);
  assert.equal(results.filter((r) => r.kind !== "page").length, 0);
  assert.equal(run("watchlist", null)[0].href, "/watchlist");
});

test("sectors are searchable and deep-link into Stock Universe", () => {
  const health = run("healthcare").find((r) => r.kind === "sector");
  assert.equal(health.href, "/universe?sector=Healthcare");
  const fin = run("financial").find((r) => r.kind === "sector");
  assert.equal(fin.label, "Financial Services");
  assert.match(fin.detail, /^4 stocks/);
  assert.equal(run("unassigned").some((r) => r.kind === "sector"), false);
});

test("liquidity breaks ties between equal matches", () => {
  assert.deepEqual(symbolsFor("hdfc").slice(0, 2), ["HDFCBANK", "HDFCAMC"]);
});

test("symbols with punctuation are matched and safely linked", () => {
  const mm = stocksFor("m&m")[0];
  assert.equal(mm.label, "M&M");
  assert.equal(mm.href, "/stock/M%26M");
});

test("empty query lists pages, including the ones the old palette was missing", () => {
  const hrefs = run("").map((r) => r.href);
  for (const h of ["/command", "/universe", "/journal", "/research/track-record", "/analytics"]) {
    assert.ok(hrefs.includes(h), h);
  }
  assert.equal(hrefs.includes("/health"), false, "admin-only page must not be listed");
});

test("a shared Portfolio destination is listed once", () => {
  const hrefs = buildSearchPages("/analytics").map((p) => p.href);
  assert.equal(new Set(hrefs).size, hrefs.length);
});

test("normalisation and distance helpers", () => {
  assert.equal(normalizeQuery("  nse:reliance.ns "), "RELIANCE");
  assert.equal(editDistance("HDFCBNAK", "HDFCBANK", 2), 1);
  assert.equal(editDistance("ABCDEFGH", "ZZZZZZZZ", 2), 3);
  assert.equal(typoBudget(3), 0);
  assert.equal(typoBudget(5), 1);
  assert.equal(typoBudget(9), 2);
});

test("terse universe sector labels answer to everyday words", () => {
  // The real stock_universe labels: "Finance", "Pharma", "IT", "Unknown".
  const real = buildStockIndex([
    { symbol: "HDFCBANK", company_name: "HDFC Bank Limited", sector: "Finance" },
    { symbol: "SUNPHARMA", company_name: "Sun Pharmaceutical Industries Limited", sector: "Pharma" },
    { symbol: "TCS", company_name: "Tata Consultancy Services Limited", sector: "IT" },
    { symbol: "NOSECTOR", company_name: "No Sector Limited", sector: "Unknown" },
  ]);
  const sectorsFor = (q) =>
    flattenGroups(searchAll(q, real, PAGES)).filter((r) => r.kind === "sector").map((r) => r.label);
  assert.ok(sectorsFor("banking").includes("Finance"));
  assert.ok(sectorsFor("healthcare").includes("Pharma"));
  assert.ok(sectorsFor("technology").includes("IT"));
  assert.ok(sectorsFor("it").includes("IT"));
  assert.deepEqual(sectorsFor("unknown"), []);
});

test("fast enough to run on every keystroke over a full-size universe", () => {
  const big = buildStockIndex(
    Array.from({ length: 3000 }, (_, i) => ({
      symbol: `SYM${i}X${(i * 7919) % 1000}`,
      company_name: `Company Number ${i} Industries Limited`,
      sector: `Sector ${i % 23}`,
    })),
  );
  const queries = ["sym12", "company number 42", "industreis", "sector 7", "zzzz", "number 2999", "s"];
  const t0 = performance.now();
  for (let k = 0; k < 20; k++) for (const q of queries) searchAll(q, big, PAGES);
  const perSearch = (performance.now() - t0) / (20 * queries.length);
  assert.ok(perSearch < 25, `avg ${perSearch.toFixed(2)}ms per search`);
});
