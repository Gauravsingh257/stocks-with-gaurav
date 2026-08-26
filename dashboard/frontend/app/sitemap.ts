/**
 * sitemap.xml
 *
 * Was 10 static URLs. The ~2,364 NSE stock pages — the entire long-tail keyword
 * surface — were absent, so nothing pointed Google at them.
 *
 * Symbols come from the weekly `stock_universe` snapshot via a thin endpoint,
 * and the fetch is cached, so building this does not hit a provider. If that
 * call fails the sitemap still emits every static route rather than 500-ing:
 * a temporarily short sitemap is recoverable, a missing one is not.
 *
 * Google's per-file cap is 50,000 URLs / 50MB uncompressed. At ~2.4k we are far
 * under it, so a single file is correct; revisit with generateSitemaps() only if
 * the universe ever approaches that.
 */

import type { MetadataRoute } from "next";
import { indexableRoutes, site } from "@/lib/site";
import { fetchSitemapSymbols, normalizeSymbol } from "@/lib/seo/stockData";

/**
 * One hour, not twelve.
 *
 * Railway (backend) and Vercel (frontend) both deploy from `main`, in parallel,
 * and Vercel usually wins. If this route builds before the symbol endpoint is
 * live it emits only the static routes — so the recovery window, not the
 * steady-state freshness, is what sets this number. A Vercel redeploy also
 * clears the data cache outright, which is the fast path; this is the safety
 * net for when the deploy sequence is not followed exactly.
 */
export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();

  const staticEntries: MetadataRoute.Sitemap = indexableRoutes.map((route) => ({
    url: `${site.url}${route}`,
    lastModified: now,
    changeFrequency: route === "/" ? "daily" : "daily",
    priority: route === "/" ? 1 : route === "/research" ? 0.9 : 0.7,
  }));

  const symbols = await fetchSitemapSymbols();
  const seen = new Set<string>();
  const stockEntries: MetadataRoute.Sitemap = [];

  for (const item of symbols) {
    const symbol = normalizeSymbol(item.symbol);
    // Upstream sorts by turnover, so duplicates would otherwise keep the first
    // (most liquid) row anyway — but dedupe explicitly, duplicate sitemap URLs
    // are a validation warning in Search Console.
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    stockEntries.push({
      url: `${site.url}/stock/${symbol}`,
      lastModified: now,
      // Fundamentals refresh weekly; SMC levels revalidate hourly. Weekly is the
      // honest signal — overstating freshness gets the hint ignored entirely.
      changeFrequency: "weekly",
      priority: 0.6,
    });
  }

  return [...staticEntries, ...stockEntries];
}
