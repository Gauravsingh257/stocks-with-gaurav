import type { MetadataRoute } from "next";
import { privateRoutes, site } from "@/lib/site";

/**
 * Crawl budget is finite and we just added ~2,364 pages that need discovering.
 * Authenticated surfaces render a sign-in wall to Googlebot, so they can never
 * rank — disallowing them redirects crawl effort to the stock pages that can.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/api/",
        "/research/chart",
        ...privateRoutes,
      ],
    },
    sitemap: `${site.url}/sitemap.xml`,
    host: site.url,
  };
}
