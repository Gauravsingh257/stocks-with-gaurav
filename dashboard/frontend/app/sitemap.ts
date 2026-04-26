import type { MetadataRoute } from "next";
import { appRoutes, site } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return appRoutes.map((route) => ({
    url: `${site.url}${route}`,
    lastModified: now,
    changeFrequency: route === "/" ? "daily" : "hourly",
    priority: route === "/" ? 1 : route === "/research" ? 0.9 : 0.6,
  }));
}
