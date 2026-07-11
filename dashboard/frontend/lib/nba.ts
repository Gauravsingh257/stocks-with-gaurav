/**
 * lib/nba.ts — Next Best Action engine, V1 (rule-based).
 *
 * Sprint 1 principle: reuse, don't invent. This engine consumes the existing
 * /api/command-center payload (CommandCenterResponse) plus a little live market
 * context and produces a ranked list of "what should I do next?" actions.
 *
 * It powers the Command Center hero, and a compact <NextBestAction> card that
 * drops onto Research / Watchlist / Terminal — so every surface ends with the
 * same, consistent "here's your next move" prompt (no dead ends).
 *
 * Deliberately simple: pure functions, rule-based scoring. No ML, no new API.
 * Option A guardrail: actions are navigational/analytical ("Review", "Study",
 * "Open"), never transactional ("Buy"/"Sell").
 */
import type { CommandCenterResponse } from "@/lib/api";

export type NBAKind =
  | "risk"        // something is deteriorating / needs protection
  | "watchlist"   // a watched name did something
  | "opportunity" // a setup worth studying
  | "market"      // market-level context / today's setup
  | "portfolio"   // portfolio needs a look
  | "explore";    // fallback — nothing personal yet

export type NBASeverity = "high" | "medium" | "low" | "info";

export interface NBAAction {
  id: string;
  kind: NBAKind;
  severity: NBASeverity;
  /** Higher = more urgent. Used to rank. */
  priority: number;
  headline: string;
  detail?: string;
  /** Where the action takes the user. */
  href: string;
  /** Button label. Navigational verbs only (Option A). */
  cta: string;
  symbol?: string;
}

export interface MarketContext {
  /** true during 09:15–15:30 IST */
  marketOpen?: boolean;
  /** true in the pre-open study window (roughly before 09:15) */
  preOpen?: boolean;
  regime?: string | null;
  pnlR?: number | null;
  /** number of symbols on the user's personal desk */
  watchlistCount?: number;
}

const SEV_WEIGHT: Record<NBASeverity, number> = { high: 100, medium: 60, low: 30, info: 12 };
const KIND_WEIGHT: Record<NBAKind, number> = {
  risk: 40, watchlist: 30, portfolio: 24, market: 18, opportunity: 16, explore: 4,
};

function sev(s?: string | null): NBASeverity {
  const v = String(s || "").toLowerCase();
  if (v === "high") return "high";
  if (v === "medium" || v === "med") return "medium";
  if (v === "low") return "low";
  return "info";
}

function stockHref(symbol?: string | null): string {
  const s = String(symbol || "").replace("NSE:", "").trim().toUpperCase();
  return s ? `/stock/${s}` : "/watchlist";
}

/**
 * Turn the command-center payload + market context into a ranked action list.
 * Rules, in priority order:
 *   1. Explicit alerts requiring attention  → RISK (high)
 *   2. Biggest deteriorations               → RISK
 *   3. "What matters now" priority lines     → mapped by severity
 *   4. Biggest improvements                  → OPPORTUNITY (readiness rising)
 *   5. Best opportunities / high conviction  → OPPORTUNITY (study)
 *   6. Market context (pre-open / regime)    → MARKET (today's setup)
 *   7. Fallback                              → EXPLORE
 */
export function computeNBA(cc: CommandCenterResponse | null, ctx: MarketContext = {}): NBAAction[] {
  const out: NBAAction[] = [];
  const push = (a: Omit<NBAAction, "priority">) => {
    out.push({ ...a, priority: SEV_WEIGHT[a.severity] + KIND_WEIGHT[a.kind] });
  };

  // 1 — alerts requiring attention (highest urgency)
  for (const al of cc?.alerts_requiring_attention ?? []) {
    if (!al?.symbol) continue;
    push({
      id: `alert-${al.symbol}`,
      kind: "risk",
      severity: sev(al.severity) === "info" ? "high" : sev(al.severity),
      headline: `${al.symbol} — ${al.action}`,
      detail: "On your desk · needs a look before anything else",
      href: stockHref(al.symbol),
      cta: `Review ${al.symbol}`,
      symbol: al.symbol,
    });
  }

  // 2 — biggest deteriorations
  for (const d of (cc?.biggest_deteriorations ?? []).slice(0, 3)) {
    if (!d?.symbol) continue;
    push({
      id: `deteriorate-${d.symbol}`,
      kind: "risk",
      severity: "medium",
      headline: `${d.symbol} weakening`,
      detail: d.validated_overall ? String(d.validated_overall).replace(/_/g, " ") : "Structure deteriorating",
      href: stockHref(d.symbol),
      cta: `Check ${d.symbol}`,
      symbol: d.symbol,
    });
  }

  // 3 — "what matters now" priority feed
  for (const [i, ln] of (cc?.what_matters_now ?? []).slice(0, 6).entries()) {
    if (!ln?.headline) continue;
    const s = sev(ln.severity);
    const kind: NBAKind = ln.kind === "deterioration" ? "risk"
      : ln.kind === "opportunity" || ln.kind === "improvement" ? "opportunity"
      : ln.kind === "promotion" || ln.kind === "tier" ? "watchlist"
      : "market";
    push({
      id: `matters-${i}`,
      kind,
      severity: s,
      headline: ln.headline,
      href: ln.symbol ? stockHref(ln.symbol) : "/watchlist",
      cta: ln.symbol ? `Open ${ln.symbol}` : "Open watchlist",
      symbol: ln.symbol ?? undefined,
    });
  }

  // 4 — biggest improvements (readiness rising)
  for (const im of (cc?.biggest_improvements ?? []).slice(0, 3)) {
    if (!im?.symbol) continue;
    push({
      id: `improve-${im.symbol}`,
      kind: "opportunity",
      severity: "low",
      headline: `${im.symbol} readiness improving`,
      detail: typeof im.readiness_delta_pct === "number" ? `${im.readiness_delta_pct >= 0 ? "+" : ""}${im.readiness_delta_pct.toFixed(1)} pts vs prior` : undefined,
      href: stockHref(im.symbol),
      cta: `Study ${im.symbol}`,
      symbol: im.symbol,
    });
  }

  // 5 — best opportunities / high conviction to study
  const opp = cc?.best_opportunities_now?.[0] ?? cc?.active_high_conviction?.[0];
  if (opp?.symbol) {
    push({
      id: `opp-${opp.symbol}`,
      kind: "opportunity",
      severity: "low",
      headline: `Study ${opp.symbol}`,
      detail: opp.note || "Surfacing in today's discovery",
      href: "/research",
      cta: "Open Research",
      symbol: opp.symbol,
    });
  }

  // 6 — market context (today's setup / regime)
  if (ctx.preOpen) {
    push({
      id: "market-preopen",
      kind: "market",
      severity: "medium",
      headline: "Build today's gameplan before the bell",
      detail: ctx.regime ? `Regime reads ${ctx.regime}` : undefined,
      href: "/research",
      cta: "Open Research",
    });
  } else if (ctx.marketOpen) {
    push({
      id: "market-open",
      kind: "market",
      severity: "low",
      headline: "Market is live — check what's triggering",
      href: "/terminal",
      cta: "Open Terminal",
    });
  }

  // 7 — fallback / explore (only if nothing personal surfaced)
  const hasPersonal = out.some((a) => a.kind === "risk" || a.kind === "watchlist");
  if (!hasPersonal && (ctx.watchlistCount ?? 0) === 0) {
    push({
      id: "explore-watchlist",
      kind: "explore",
      severity: "info",
      headline: "Start monitoring your first stock",
      detail: "Add a name and the desk watches it for you",
      href: "/research",
      cta: "Find a stock",
    });
  }

  // Rank + de-dupe by symbol+kind (keep highest priority)
  out.sort((a, b) => b.priority - a.priority);
  const seen = new Set<string>();
  const ranked: NBAAction[] = [];
  for (const a of out) {
    const key = `${a.kind}:${a.symbol ?? a.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    ranked.push(a);
  }
  return ranked;
}

/** The single highest-priority action, or null. */
export function topNBA(cc: CommandCenterResponse | null, ctx: MarketContext = {}): NBAAction | null {
  return computeNBA(cc, ctx)[0] ?? null;
}

/** IST market phase helper (09:15–15:30 open; before → pre-open on weekdays). */
export function marketPhase(now: Date = new Date()): { marketOpen: boolean; preOpen: boolean } {
  // Convert to IST (UTC+5:30) regardless of viewer timezone.
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  const ist = new Date(utc + 5.5 * 3600000);
  const day = ist.getDay(); // 0 Sun … 6 Sat
  const mins = ist.getHours() * 60 + ist.getMinutes();
  const weekday = day >= 1 && day <= 5;
  const marketOpen = weekday && mins >= 555 && mins <= 930;   // 09:15–15:30
  const preOpen = weekday && mins >= 420 && mins < 555;        // 07:00–09:15
  return { marketOpen, preOpen };
}
