// Unit tests for lib/displayLabels.ts — no advice wording ever reaches a visitor.
// Run: node --test dashboard/frontend/lib/displayLabels.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { actionText, directionText, recommendationText } from "./displayLabels.ts";

const ADVICE = /\b(buy|sell|avoid|accumulate|hold)\b/i;

test("stock confluence tiers are descriptive", () => {
  assert.equal(recommendationText("Strong Buy"), "High confluence");
  assert.equal(recommendationText("Watchlist"), "Moderate confluence");
  assert.equal(recommendationText("Avoid"), "Low confluence");
  assert.equal(recommendationText(null), "—");
  assert.equal(recommendationText("Something new"), "Confluence n/a");
});

test("terminal quality tiers are descriptive", () => {
  assert.equal(actionText("STRONG BUY"), "TOP SETUP");
  assert.equal(actionText("BUY"), "QUALIFIED");
  assert.equal(actionText("WATCH"), "MONITOR");
  assert.equal(actionText("AVOID"), "LOW QUALITY");
  assert.equal(actionText("buy"), "QUALIFIED");
});

test("directions read as sides, not instructions", () => {
  assert.equal(directionText("BUY"), "LONG");
  assert.equal(directionText("SELL"), "SHORT");
  assert.equal(directionText("LONG"), "LONG");
});

test("no mapped label contains advice wording", () => {
  for (const v of ["Strong Buy", "Watchlist", "Avoid"]) assert.doesNotMatch(recommendationText(v), ADVICE);
  for (const v of ["STRONG BUY", "BUY", "WATCH", "AVOID"]) assert.doesNotMatch(actionText(v), ADVICE);
  for (const v of ["BUY", "SELL"]) assert.doesNotMatch(directionText(v), ADVICE);
});
