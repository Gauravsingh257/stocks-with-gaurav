import { site } from "@/lib/site";

/**
 * Site-wide structured data, emitted once from the root layout.
 *
 * Two things it deliberately does NOT claim:
 *  - no `FinancialService` / `Review` / `AggregateRating` types, which signal a
 *    rated advisory product. The site is educational research and not
 *    SEBI-registered, so the markup must not imply otherwise.
 *  - no `sameAs` links until real, verified profile URLs exist — pointing at
 *    profiles that are not provably the same entity is a spam signal.
 *
 * The `SearchAction` makes the site eligible for a Google sitelinks searchbox.
 * It points at /research, which hosts the global NSE symbol search.
 */
export default function SiteJsonLd() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${site.url}/#organization`,
        name: site.name,
        url: site.url,
        description: site.description,
        logo: {
          "@type": "ImageObject",
          url: `${site.url}/opengraph-image`,
        },
      },
      {
        "@type": "WebSite",
        "@id": `${site.url}/#website`,
        name: site.name,
        url: site.url,
        description: site.description,
        publisher: { "@id": `${site.url}/#organization` },
        inLanguage: "en-IN",
        potentialAction: {
          "@type": "SearchAction",
          target: {
            "@type": "EntryPoint",
            urlTemplate: `${site.url}/research?q={search_term_string}`,
          },
          "query-input": "required name=search_term_string",
        },
      },
    ],
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
