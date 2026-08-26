export const site = {
  name: "Stocks With Gaurav",
  url: "https://stockswithgaurav.com",
  title: "Stocks With Gaurav | Smart Money Concepts Research Dashboard",
  description:
    "Educational Smart Money Concepts research dashboard for NSE market structure, watchlists, trade journaling, and transparent algorithmic research.",
  ogImage: "/opengraph-image",
};

/**
 * Routes that render real content to a logged-out visitor, and therefore to
 * Googlebot. Everything here goes into sitemap.xml.
 *
 * `/watchlist` and `/screeners` were previously listed but both render a sign-in
 * wall without a session, so submitting them was feeding Google thin pages.
 * `/login` and `/register` are crawlable but deliberately absent — a sitemap is
 * a list of pages you want ranked, and auth forms are not that.
 */
export const indexableRoutes = [
  "/",
  "/research",
  "/research/track-record",
  "/universe",
];

/**
 * Private app surfaces — disallowed in robots.txt so crawl budget goes to the
 * ~2,364 stock pages instead of login walls that can never rank.
 *
 * Bare paths, no trailing slash: `Disallow: /journal` covers both `/journal`
 * and everything beneath it, whereas `/journal/` would leave the page itself
 * crawlable.
 */
export const privateRoutes = [
  "/journal",
  "/analytics",
  "/intelligence",
  "/terminal",
  "/command",
  "/health",
  "/agents",
  "/risk-engine",
  "/oi-intelligence",
  "/market-intelligence",
];

/**
 * Login-walled today, but genuine product pages that could be opened up later.
 * Kept out of the sitemap (nothing to rank yet) and *not* robots-disallowed, so
 * making them public is a one-line change rather than a re-crawl negotiation.
 */
export const excludedFromSitemap = ["/watchlist", "/screeners", "/login", "/register"];

/** @deprecated use `indexableRoutes` — kept so nothing silently breaks. */
export const appRoutes = indexableRoutes;
