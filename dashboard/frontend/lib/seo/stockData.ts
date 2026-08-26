/**
 * lib/seo/stockData.ts — server-only data access for the public /stock/<symbol> page.
 *
 * Deliberately NOT lib/api.ts: that client sets `cache: "no-store"` on every GET,
 * which is correct for a live trading dashboard and fatal for ISR. These helpers
 * opt into Next's data cache so Googlebot and cold visitors are served a warm page.
 *
 * Two tiers, because the two upstreams have very different reliability:
 *
 *   Tier 1  /api/research/universe/symbol/<s>  — single SQLite row, no provider
 *           calls. Fast and dependable, so the page render blocks on it.
 *   Tier 2  /api/search-stock?symbol=<s>       — yfinance 420d OHLC + a NIFTY 50
 *           fetch (which is known to be blocked on Railway) behind a 300s
 *           in-process cache. Seconds when warm, a failure when not.
 *
 * Blocking a 2,364-page render surface on Tier 2 would emit timeouts and
 * soft-404s, so Tier 2 is strictly best-effort: it enriches the HTML when it
 * answers in time and is otherwise picked up client-side, leaving Tier 1 as the
 * indexable floor.
 */

// NOTE: no `server-only` guard — the package is not a dependency and adding one
// to a live build is not worth it here. These helpers are imported solely by
// server components; keep it that way (the fetch cache is a no-op in a client).
import { getBackendBase } from "@/lib/api";
import type { StockAnalysis } from "@/lib/api";

/** Universe snapshot refreshes weekly (Sat 09:00), so a day is already generous. */
const UNIVERSE_REVALIDATE_SEC = 60 * 60 * 12;
/** Analysis is intraday-sensitive; an hour keeps pages fresh without hammering yfinance. */
const ANALYSIS_REVALIDATE_SEC = 60 * 60;
/**
 * Shorter than the universe TTL on purpose. The sitemap has a cliff the stock
 * pages do not: if it is built while the backend lacks the symbol endpoint it
 * bakes a handful of static URLs and every stock page becomes undiscoverable
 * for a full cache cycle. An hour bounds that blast radius. It is cheap — one
 * thin three-column read per hour.
 */
const SITEMAP_REVALIDATE_SEC = 60 * 60;
/** Hard ceiling on the render path. Past this we ship Tier 1 and let the client finish. */
const ANALYSIS_TIMEOUT_MS = 6_000;

export interface UniverseRow {
  symbol: string;
  company_name: string | null;
  sector: string | null;
  instrument: string | null;
  price: number | null;
  market_cap_cr: number | null;
  turnover_cr: number | null;
  pe: number | null;
  pb: number | null;
  roe_pct: number | null;
  debt_to_equity: number | null;
  revenue_growth_pct: number | null;
  net_margin_pct: number | null;
  promoter_pct: number | null;
  pct_from_52w_high: number | null;
  ret_1y_pct: number | null;
  refreshed_at: string | null;
}

/** Strip NSE:/​.NS decoration and any character that can't appear in an NSE ticker. */
export function normalizeSymbol(raw: string): string {
  return decodeURIComponent(raw || "")
    .toUpperCase()
    .replace(/^NSE:/, "")
    .replace(/\.NS$/, "")
    .replace(/[^A-Z0-9&-]/g, "")
    .slice(0, 32);
}

function apiUrl(path: string): string | null {
  const base = getBackendBase();
  // Server-side fetch cannot use the next.config rewrite — it needs an origin.
  // Without one we return null so callers degrade instead of throwing at build.
  return base ? `${base}${path}` : null;
}

/**
 * Tier 1 — blocking.
 *
 * The three outcomes are kept distinct on purpose. Collapsing "backend
 * unreachable" into "unknown symbol" would make a single Railway blip return
 * 404 for all ~2,364 stock URLs at once, and Google deindexes on repeated 404s
 * far faster than it reindexes. So only an explicit upstream 404 is allowed to
 * become a 404; anything else degrades to a thin-but-valid 200.
 */
export type UniverseLookup =
  | { status: "ok"; row: UniverseRow }
  | { status: "notfound" }
  | { status: "unavailable" };

export async function fetchUniverseRow(symbol: string): Promise<UniverseLookup> {
  const url = apiUrl(`/api/research/universe/symbol/${encodeURIComponent(symbol)}`);
  if (!url) return { status: "unavailable" };
  try {
    const res = await fetch(url, { next: { revalidate: UNIVERSE_REVALIDATE_SEC } });

    // An HTTP 404 here means the *endpoint* is missing — an older backend, a
    // rollback, a bad deploy — never that the symbol is unknown. The route
    // signals an unknown symbol with 200 + reason:"not_in_universe" precisely
    // so these two cannot be confused. Treating a route-404 as "notfound" would
    // 404 every stock page the moment frontend and backend drift apart.
    if (!res.ok) return { status: "unavailable" };

    const json = (await res.json()) as {
      available?: boolean;
      item?: UniverseRow | null;
      reason?: string;
    };
    if (json?.available && json.item) return { status: "ok", row: json.item };
    return json?.reason === "not_in_universe"
      ? { status: "notfound" }
      : { status: "unavailable" };
  } catch {
    return { status: "unavailable" };
  }
}

/**
 * Tier 2 — best-effort. Never throws; a null here costs enrichment, not the page.
 *
 * The render budget is enforced with Promise.race rather than AbortSignal, and
 * that choice is load-bearing: Next refuses to cache any fetch carrying a
 * `signal`, so an AbortSignal here silently opted the whole route out of ISR and
 * sent every single crawl back through yfinance.
 *
 * Racing instead means a slow call loses the race but keeps running, and its
 * response still populates the data cache — so the visitor who times out warms
 * the page for the next one, and repeat crawls get the analysis server-rendered.
 */
export async function fetchStockAnalysis(symbol: string): Promise<StockAnalysis | null> {
  const url = apiUrl(`/api/search-stock?symbol=${encodeURIComponent(symbol)}`);
  if (!url) return null;

  const request = fetch(url, { next: { revalidate: ANALYSIS_REVALIDATE_SEC } })
    .then(async (res) => (res.ok ? ((await res.json()) as StockAnalysis) : null))
    // Swallow here so losing the race can never surface as an unhandled rejection.
    .catch(() => null);

  const budget = new Promise<null>((resolve) => {
    const timer = setTimeout(() => resolve(null), ANALYSIS_TIMEOUT_MS);
    // Don't hold the serverless function open purely for the timer.
    if (typeof timer === "object" && timer && "unref" in timer) {
      (timer as { unref: () => void }).unref();
    }
  });

  return Promise.race([request, budget]);
}

export interface SitemapSymbol {
  symbol: string;
  company_name: string | null;
  sector: string | null;
  refreshed_at: string | null;
}

/** Symbol list for sitemap.xml. Empty array on failure keeps the static routes valid. */
export async function fetchSitemapSymbols(): Promise<SitemapSymbol[]> {
  const url = apiUrl("/api/research/universe/sitemap?limit=5000");
  if (!url) return [];
  try {
    const res = await fetch(url, { next: { revalidate: SITEMAP_REVALIDATE_SEC } });
    if (!res.ok) return [];
    const json = (await res.json()) as { items?: SitemapSymbol[] };
    return Array.isArray(json?.items) ? json.items : [];
  } catch {
    return [];
  }
}

/**
 * Sector peers for the "related stocks" block — the internal-linking surface that
 * turns 2,364 orphan pages into a crawlable graph. Sorted by turnover upstream, so
 * taking the head gives the most liquid (and most searched) names in the sector.
 */
export async function fetchSectorPeers(
  sector: string | null,
  excludeSymbol: string,
  limit = 12,
): Promise<SitemapSymbol[]> {
  if (!sector) return [];
  const url = apiUrl(
    `/api/research/universe?sector=${encodeURIComponent(sector)}&limit=200`,
  );
  if (!url) return [];
  try {
    const res = await fetch(url, { next: { revalidate: UNIVERSE_REVALIDATE_SEC } });
    if (!res.ok) return [];
    const json = (await res.json()) as { items?: (SitemapSymbol & { symbol: string })[] };
    const items = Array.isArray(json?.items) ? json.items : [];
    return items
      .filter((r) => normalizeSymbol(r.symbol) !== excludeSymbol)
      .slice(0, limit);
  } catch {
    return [];
  }
}

/** URL-safe sector slug. Kept here so the page and the future /sector route agree. */
export function sectorSlug(sector: string | null | undefined): string | null {
  if (!sector) return null;
  const slug = sector.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return slug || null;
}
