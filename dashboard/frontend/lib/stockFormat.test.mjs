// Unit tests for lib/stockFormat.ts.
// Run: node --test dashboard/frontend/lib/stockFormat.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  crore,
  inr,
  istDateTime,
  istDay,
  num,
  quoteSourceLabel,
  signedPct,
  snapshotDate,
} from "./stockFormat.ts";

test("one fact is written one way, and missing values read as a dash", () => {
  assert.equal(num(222.00002, "x"), "222x");
  assert.equal(num(15.175702, "x"), "15.18x");
  assert.equal(inr(57.72), "₹57.72");
  assert.equal(crore(6721.5), "₹6,722 Cr");
  assert.equal(crore(1091700.7), "₹10.92 lakh Cr");
  assert.equal(signedPct(-27.6), "−27.6%");
  assert.equal(signedPct(140.52), "+140.52%");
  for (const v of [null, undefined, Number.NaN]) {
    assert.equal(num(v, "x"), "—");
    assert.equal(inr(v), "—");
    assert.equal(crore(v), "—");
  }
});

test("snapshot timestamps are UTC and shown as an IST date", () => {
  assert.match(snapshotDate("2026-09-12 03:39:16"), /^12 Sept? 2026$/);
  // 20:00 UTC on the 12th is already the 13th in IST.
  assert.match(snapshotDate("2026-09-12 20:00:00"), /^13 Sept? 2026$/);
  assert.equal(snapshotDate(null), null);
  assert.equal(snapshotDate("not a date"), null);
});

test("quote timestamps map onto IST trading days comparable with candle dates", () => {
  assert.equal(istDay("2026-09-15T07:05:28.405473+00:00"), "2026-09-15");
  assert.equal(istDay("2026-09-14T19:00:00Z"), "2026-09-15");
  assert.equal(istDay("2026-09-12 03:39:16"), "2026-09-12");
  assert.match(istDateTime("2026-09-15T07:05:28+00:00"), /^15 Sept?, 12:35 IST$/);
});

test("price sources get plain labels", () => {
  assert.equal(quoteSourceLabel("kite_live"), "Live");
  assert.equal(quoteSourceLabel("ws_cache"), "Live");
  assert.equal(quoteSourceLabel("yf_delayed"), "Delayed");
  assert.equal(quoteSourceLabel("ohlc_close"), "Last close");
  assert.equal(quoteSourceLabel("scan_snapshot"), "Last close");
  assert.equal(quoteSourceLabel(undefined), "Latest");
  assert.equal(quoteSourceLabel("unknown"), "Latest");
});
