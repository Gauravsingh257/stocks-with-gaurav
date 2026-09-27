/**
 * User-facing wording for internal decision values.
 *
 * The site is analytics, not advice (not SEBI-registered), so values such as
 * "Strong Buy" or "AVOID" must never be shown to a visitor. The INTERNAL values
 * are unchanged — scoring, colours and every `=== "Avoid"` / `=== "BUY"` branch
 * keep comparing against them. Only the text a person reads is mapped here,
 * describing what was measured rather than an action to take.
 */

/** Stock analysis confluence tier (services/stock_search_analysis._recommendation). */
const RECOMMENDATION_TEXT: Record<string, string> = {
  "Strong Buy": "High confluence",
  Watchlist: "Moderate confluence",
  Avoid: "Low confluence",
};

/** Terminal setup-quality tier (dashboard/backend/intelligence.py). */
const ACTION_TEXT: Record<string, string> = {
  "STRONG BUY": "TOP SETUP",
  BUY: "QUALIFIED",
  WATCH: "MONITOR",
  AVOID: "LOW QUALITY",
};

/** Setup direction: the side a pattern points to, not an instruction. */
const DIRECTION_TEXT: Record<string, string> = { BUY: "LONG", SELL: "SHORT", LONG: "LONG", SHORT: "SHORT" };

export const recommendationText = (value: string | null | undefined): string =>
  (value && RECOMMENDATION_TEXT[value]) || (value ? "Confluence n/a" : "—");

export const actionText = (value: string | null | undefined): string =>
  (value && ACTION_TEXT[value.toUpperCase()]) || (value ? value.toUpperCase() : "—");

export const directionText = (value: string | null | undefined): string =>
  (value && DIRECTION_TEXT[value.toUpperCase()]) || (value ?? "—");
