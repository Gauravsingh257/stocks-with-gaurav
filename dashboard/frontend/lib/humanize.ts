/**
 * lib/humanize.ts — presentation-only copy softener.
 *
 * The command-center / watchlist backend emits desk jargon ("institutional-grade
 * desk posture", "deterioration risk elevated", "likely_invalidation"). Rather
 * than rewrite strings across several backend services (and risk other
 * consumers), we translate to plain trader language at render time. Pure,
 * presentational, reversible.
 *
 * Option A: every replacement stays analytical/observational — describing what
 * the market is doing, never instructing a buy/sell.
 */

// Ordered — earlier rules run first so specific phrases win over generic ones.
const RULES: [RegExp, string][] = [
  [/institutional[-\s]?grade desk posture/gi, "a high-quality setup"],
  [/\(modelled tier\)/gi, ""],
  [/modelled tier/gi, ""],
  [/promoted toward final review posture\s*[—-]\s*gates still apply/gi, "strengthening toward a tradeable level"],
  [/final[-\s]?review bucket\s*[—-]\s*risk[-\s]?map still required\.?/gi, "worth a closer look."],
  [/nearing institutional[-\s]?grade/gi, "building into a strong"],
  [/deterioration risk elevated/gi, "losing strength"],
  [/structural\s+deterioration(\s+confirmed)?/gi, "losing strength"],
  [/deterioration validation|deterioration validated/gi, "confirmed weakening"],
  [/likely[_\s]invalidation/gi, "setup may be breaking down"],
  [/capital[_\s]protection(\s+mode)?/gi, "protect capital"],
  [/avoid[_\s]entry/gi, "not an entry yet"],
  [/momentum[_\s]chasing risk/gi, "don't chase"],
  [/readiness improving vs prior snapshot/gi, "getting stronger"],
  [/readiness delta/gi, "strength change"],
  [/\breadiness\b/gi, "strength"],
  [/\bposture\b/gi, "setup"],
  [/\bnearing\b/gi, "building toward"],
  [/desk\b/gi, ""],
];

/** Softens a single institutional string into plain trader language. */
export function humanize(input?: string | null): string {
  if (!input) return "";
  let s = String(input);
  for (const [re, rep] of RULES) s = s.replace(re, rep);
  // tidy: collapse doubled spaces; tighten spaces before sentence punctuation
  // ONLY (never dashes — " — " is intentional spacing); trim leading separators.
  s = s.replace(/\s{2,}/g, " ").replace(/\s+([,.;:])/g, "$1").trim();
  s = s.replace(/^[\s—:-]+/, "");
  // capitalize the first letter (rules can strip a leading word e.g. "Desk ")
  if (s) s = s.charAt(0).toUpperCase() + s.slice(1);
  return s;
}

/** Friendly label for a raw `validated_overall` / status code. */
export function friendlyStatus(code?: string | null): string {
  const v = String(code || "").toLowerCase();
  const map: Record<string, string> = {
    likely_invalidation: "setup may be breaking down",
    capital_protection: "protect capital",
    avoid_entry: "not an entry yet",
    confirmed_deterioration: "confirmed weakening",
  };
  return map[v] || humanize(v.replace(/_/g, " "));
}

/**
 * A one-line "so what" read of the market regime — general market context, not
 * a per-stock instruction (Option A). Powers the Command Center mood banner.
 */
export function regimeContext(regime?: string | null): string {
  const v = String(regime || "").toUpperCase();
  if (v.includes("BULL"))
    return "Broad strength today — structure favours longs. Let setups come to you; don't chase extended moves.";
  if (v.includes("BEAR"))
    return "Pressure to the downside — stay selective and protect capital. Rallies can fade quickly.";
  if (v.includes("CHOP") || v.includes("RISK") || v.includes("SIDE"))
    return "Choppy, two-way tape — false breaks are common. Wait for confirmation near your levels.";
  return "No clear edge in the tape — patience beats forcing trades. Focus only on your cleanest setups.";
}
