/**
 * /stock/<symbol> — the public, indexable stock page.
 *
 * Previously a `"use client"` page that fetched in useEffect, which meant every
 * one of the ~2,364 NSE symbols served Googlebot the same root <title> and a body
 * containing only "Loading stock analysis...". This is now a server component so
 * the crawler receives a unique title, description, canonical and real content.
 *
 * Render budget is protected by the two-tier split in lib/seo/stockData.ts: the
 * fundamentals below come from a single SQLite read, while the SMC analysis is
 * best-effort and degrades to a client fetch. See that file for why.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import {
  fetchSectorPeers,
  fetchStockAnalysis,
  fetchUniverseRow,
  normalizeSymbol,
  sectorSlug,
  type UniverseRow,
} from "@/lib/seo/stockData";
import { site } from "@/lib/site";
import StockAnalysisPanel from "./StockAnalysisPanel";

/**
 * Empty on purpose: zero pages are pre-built, so a 2,113-symbol universe never
 * touches build time. But exporting it at all is what marks the route
 * statically-generatable — without it Next renders every request dynamically
 * and Vercel sends `no-store`, so each of the 2,113 URLs re-rendered on every
 * single crawl. With it, `dynamicParams` renders tail symbols on demand and
 * then caches them, which is the ISR behaviour this route always intended.
 */
export function generateStaticParams() {
  return [];
}

export const dynamicParams = true;
export const revalidate = 43200;

type PageProps = { params: Promise<{ symbol: string }> };

function displayName(row: UniverseRow | null, symbol: string): string {
  const name = row?.company_name?.trim();
  return name && name.toUpperCase() !== symbol ? name : symbol;
}

function inr(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function num(value: number | null | undefined, suffix = ""): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${suffix}`;
}

function crore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (value >= 100000) return `₹${(value / 100000).toFixed(2)} lakh Cr`;
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr`;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const symbol = normalizeSymbol((await params).symbol);
  if (!symbol) return { title: "Stock not found", robots: { index: false, follow: false } };

  const lookup = await fetchUniverseRow(symbol);
  const row = lookup.status === "ok" ? lookup.row : null;
  const name = displayName(row, symbol);
  const sector = row?.sector?.trim();
  // Only a symbol the universe actively denies is noindexed. A transient
  // "unavailable" must inherit the default index directive: emitting noindex
  // during a backend blip would drop the whole long tail out of the index, and
  // Google acts on noindex far faster than it re-includes a page afterwards.
  const isThin = lookup.status === "notfound";

  // Every page gets its own title/description — the whole point of the change.
  const title = `${name} (${symbol}) Share Price, Chart & SMC Analysis — NSE`;
  const bits = [
    `Track ${name} (NSE: ${symbol})`,
    sector ? `a ${sector} stock` : null,
    "with Smart Money Concepts market structure, order blocks, fair value gaps and key fundamentals.",
    "Educational research only — not investment advice.",
  ].filter(Boolean);

  return {
    title,
    description: bits.join(" ").slice(0, 300),
    alternates: { canonical: `/stock/${symbol}` },
    openGraph: {
      title,
      description: bits.join(" ").slice(0, 200),
      url: `${site.url}/stock/${symbol}`,
      type: "website",
    },
    twitter: { card: "summary_large_image", title },
    ...(isThin ? { robots: { index: false, follow: true } } : {}),
  };
}

export default async function StockDetailPage({ params }: PageProps) {
  const symbol = normalizeSymbol((await params).symbol);
  if (!symbol) notFound();

  const lookup = await fetchUniverseRow(symbol);
  if (lookup.status === "notfound") notFound();
  const row = lookup.status === "ok" ? lookup.row : null;

  // Tier 2 and peers are both non-blocking extras; run them together.
  const [analysis, peers] = await Promise.all([
    fetchStockAnalysis(symbol),
    fetchSectorPeers(row?.sector ?? null, symbol),
  ]);

  const name = displayName(row, symbol);
  const sector = row?.sector?.trim() || null;
  const slug = sectorSlug(sector);

  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Home", item: site.url },
          { "@type": "ListItem", position: 2, name: "Research", item: `${site.url}/research` },
          ...(sector
            ? [
                {
                  "@type": "ListItem",
                  position: 3,
                  name: sector,
                  item: `${site.url}/universe?sector=${encodeURIComponent(sector)}`,
                },
              ]
            : []),
          {
            "@type": "ListItem",
            position: sector ? 4 : 3,
            name: `${name} (${symbol})`,
            item: `${site.url}/stock/${symbol}`,
          },
        ],
      },
      {
        // Describes the *page*, not a rated product — deliberately avoids
        // Review/Rating markup, which would read as a buy/sell recommendation.
        "@type": "WebPage",
        name: `${name} (${symbol}) Share Price & SMC Analysis`,
        url: `${site.url}/stock/${symbol}`,
        isPartOf: { "@type": "WebSite", name: site.name, url: site.url },
        about: {
          "@type": "Corporation",
          name,
          tickerSymbol: `NSE:${symbol}`,
          ...(sector ? { industry: sector } : {}),
        },
      },
    ],
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav
        aria-label="Breadcrumb"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          fontSize: "0.78rem",
        }}
      >
        <Link
          href="/research"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            color: "var(--accent)",
            textDecoration: "none",
            fontWeight: 650,
          }}
        >
          <ArrowLeft size={15} /> Research
        </Link>
        <span style={{ color: "var(--text-dim)" }}>/</span>
        {sector && (
          <>
            <Link
              href={`/universe?sector=${encodeURIComponent(sector)}`}
              style={{ color: "var(--text-secondary)", textDecoration: "none" }}
            >
              {sector}
            </Link>
            <span style={{ color: "var(--text-dim)" }}>/</span>
          </>
        )}
        <span style={{ color: "var(--text-secondary)" }}>{symbol}</span>
      </nav>

      <header style={{ display: "grid", gap: 6 }}>
        <h1 style={{ margin: 0, fontSize: "1.45rem", fontWeight: 800 }}>
          {name}{" "}
          <span style={{ color: "var(--text-secondary)", fontWeight: 700 }}>
            (NSE: {symbol})
          </span>
        </h1>
        <p
          style={{
            margin: 0,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
            maxWidth: "72ch",
          }}
        >
          {name} trades on the NSE under the ticker <strong>{symbol}</strong>
          {sector ? (
            <>
              {" "}
              within the <strong>{sector}</strong> sector
            </>
          ) : null}
          . This page presents an automated Smart Money Concepts study of {symbol} — market
          structure, order blocks and fair value gaps — alongside headline fundamentals. It is
          educational research, not a recommendation to buy, sell or hold.
        </p>
      </header>

      {/* Tier 1: always present in the server HTML, even if the SMC call failed. */}
      <section className="glass" style={{ padding: 16, display: "grid", gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>{symbol} key metrics</h2>
        {row ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
              gap: 10,
            }}
          >
            <Metric label="Last price" value={inr(row.price)} />
            <Metric label="Market cap" value={crore(row.market_cap_cr)} />
            <Metric label="P/E ratio" value={num(row.pe, "x")} />
            <Metric label="P/B ratio" value={num(row.pb, "x")} />
            <Metric label="ROE" value={num(row.roe_pct, "%")} />
            <Metric label="Debt / equity" value={num(row.debt_to_equity, "x")} />
            <Metric label="Revenue growth" value={num(row.revenue_growth_pct, "%")} />
            <Metric label="Net margin" value={num(row.net_margin_pct, "%")} />
            <Metric label="Promoter holding" value={num(row.promoter_pct, "%")} />
            <Metric label="From 52w high" value={num(row.pct_from_52w_high, "%")} />
            <Metric label="1-year return" value={num(row.ret_1y_pct, "%")} />
            <Metric label="Sector" value={sector ?? "—"} />
          </div>
        ) : (
          <p style={{ margin: 0, color: "var(--text-secondary)" }}>
            Fundamental data for {symbol} is being refreshed and will appear shortly.
          </p>
        )}
        {row?.refreshed_at && (
          <p style={{ margin: 0, fontSize: "0.68rem", color: "var(--text-dim)" }}>
            Fundamentals snapshot last refreshed {row.refreshed_at} · prices may be delayed up to
            15 minutes.
          </p>
        )}
      </section>

      {/* Tier 2: SSR'd when the server got it in time, client-fetched otherwise. */}
      <StockAnalysisPanel symbol={symbol} initial={analysis} />

      {peers.length > 0 && (
        <section className="glass" style={{ padding: 16, display: "grid", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>
            Other {sector} stocks on NSE
          </h2>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {peers.map((peer) => {
              const peerSymbol = normalizeSymbol(peer.symbol);
              return (
                <Link
                  key={peerSymbol}
                  href={`/stock/${peerSymbol}`}
                  title={peer.company_name ?? peerSymbol}
                  style={{
                    fontSize: "0.76rem",
                    padding: "5px 10px",
                    borderRadius: 999,
                    border: "1px solid var(--border)",
                    background: "rgba(255,255,255,0.02)",
                    color: "var(--text-secondary)",
                    textDecoration: "none",
                    fontWeight: 650,
                  }}
                >
                  {peerSymbol}
                </Link>
              );
            })}
          </div>
          {slug && (
            <Link
              href={`/universe?sector=${encodeURIComponent(sector ?? "")}`}
              style={{
                fontSize: "0.78rem",
                color: "var(--accent)",
                textDecoration: "none",
                fontWeight: 650,
              }}
            >
              Browse all {sector} stocks →
            </Link>
          )}
        </section>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "9px 11px",
        background: "rgba(255,255,255,0.02)",
      }}
    >
      <div
        style={{
          color: "var(--text-dim)",
          fontSize: "0.66rem",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}
      >
        {label}
      </div>
      <div style={{ color: "var(--text-primary)", fontWeight: 800, marginTop: 3 }}>{value}</div>
    </div>
  );
}
