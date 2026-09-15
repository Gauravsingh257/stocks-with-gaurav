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
 *
 * Source of truth — each fact on this page has exactly one source:
 *   - company, sector, P/E, P/B, ROE, D/E, margins, growth, promoter holding,
 *     market cap → the weekly `stock_universe` snapshot, labelled with its date.
 *     The analysis card shows the same row (`analysis.reference`).
 *   - current price → services/price_resolver (live cache → Kite → delayed) via
 *     the analysis response, labelled with its source and time; shown in the
 *     analysis card and the chart caption.
 *   - the snapshot's own close is shown only as "the price these ratios use".
 *   - daily price history → /api/research/chart-data.
 * The analyzer's `fundamentals` object is its confidence input, never displayed
 * as company facts for a universe stock.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import {
  fetchSectorRows,
  fetchStockAnalysis,
  fetchUniverseRow,
  normalizeSymbol,
  sectorSlug,
  type UniverseRow,
} from "@/lib/seo/stockData";
import {
  read52WeekHigh,
  readDebtToEquity,
  readMarketCap,
  readNetMargin,
  readOneYearReturn,
  readPB,
  readPE,
  readPromoterHolding,
  readROE,
  readRevenueGrowth,
  sectorBenchmarks,
  type Reading,
  type SectorBenchmarks,
  type Tone,
} from "@/lib/metricInterpretation";
import { site } from "@/lib/site";
import { crore, inr, num, signedPct, snapshotDate } from "@/lib/stockFormat";
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
  const sector = row?.sector?.trim() || null;

  // Tier 2 and the sector list are both non-blocking extras; run them together.
  const [analysis, sectorRows] = await Promise.all([
    fetchStockAnalysis(symbol),
    fetchSectorRows(sector),
  ]);

  const name = displayName(row, symbol);
  const slug = sectorSlug(sector);
  const peers = sectorRows.filter((r) => normalizeSymbol(r.symbol) !== symbol).slice(0, 12);
  const benchmarks = sectorBenchmarks(sector, sectorRows);
  const asOf = snapshotDate(row?.refreshed_at);

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
      <section className="glass" style={{ padding: 18, display: "grid", gap: 16 }}>
        <div style={{ display: "grid", gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 800 }}>{symbol} key metrics</h2>
          {row && (
            <>
              <div className="metric-legend" aria-label="What the labels mean">
                {LEGEND.map(({ tone, label }) => (
                  <span key={tone} className="metric-pill" data-tone={tone}>
                    <span aria-hidden className="metric-pill-icon">{TONE_ICON[tone]}</span>
                    {label}
                  </span>
                ))}
              </div>
              <p style={{ margin: 0, fontSize: "0.8rem", lineHeight: 1.5, color: "var(--text-secondary)", maxWidth: "80ch" }}>
                {asOf ? `Company, sector and ratios are from the ${asOf} weekly snapshot. ` : ""}
                {benchmarks.sector
                  ? `Valuation and margin labels compare ${symbol} with ${benchmarks.count} ${benchmarks.sector} stocks (the NSE-wide median where the sector has too few); other labels use fixed ranges.`
                  : `Valuation and margin labels compare ${symbol} with the NSE-wide median; other labels use fixed ranges.`}{" "}
                Descriptive labels for education, not investment advice.
              </p>
            </>
          )}
        </div>
        {row ? (
          <div className="metric-groups">
            {metricGroups(row, sector, benchmarks, asOf).map((group) => (
              <div key={group.title}>
                <h3 className="metric-group-title">{group.title}</h3>
                <div className="metric-grid">
                  {group.items.map((item) => (
                    <MetricCard key={item.label} item={item} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p style={{ margin: 0, color: "var(--text-secondary)" }}>
            Fundamental data for {symbol} is being refreshed and will appear shortly.
          </p>
        )}
        {row?.refreshed_at && (
          <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--text-dim)" }}>
            Stock universe snapshot, refreshed weekly · last refresh {row.refreshed_at} UTC. The
            current price is shown with the chart.
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

const TONE_ICON: Record<Tone, string> = {
  good: "✓",
  caution: "!",
  weak: "✕",
  neutral: "•",
  unavailable: "–",
};

const LEGEND: { tone: Tone; label: string }[] = [
  { tone: "good", label: "Healthy" },
  { tone: "caution", label: "Caution" },
  { tone: "weak", label: "Weak" },
  { tone: "unavailable", label: "Not available" },
];

interface MetricItem {
  label: string;
  value: string;
  /** A plain-English reading; omitted for values that need none (price). */
  reading?: Reading;
  /** Context shown instead of a reading. */
  hint?: string;
}

function metricGroups(
  row: UniverseRow,
  sector: string | null,
  benchmarks: SectorBenchmarks,
  asOf: string | null,
): { title: string; items: MetricItem[] }[] {
  return [
    {
      title: "Valuation",
      items: [
        {
          // The snapshot's close, labelled for what it is: the basis of P/E, P/B and
          // market cap. The current price (price resolver) is shown with the chart.
          label: "Price used for ratios",
          value: inr(row.price),
          hint: `${asOf ? `Closing price in the ${asOf} snapshot` : "Snapshot closing price"} that P/E, P/B and market cap are based on. The current price is shown with the chart.`,
        },
        { label: "Market cap", value: crore(row.market_cap_cr), reading: readMarketCap(row.market_cap_cr) },
        { label: "P/E ratio", value: num(row.pe, "x"), reading: readPE(row.pe, row.net_margin_pct, benchmarks) },
        { label: "P/B ratio", value: num(row.pb, "x"), reading: readPB(row.pb, row.roe_pct, benchmarks) },
      ],
    },
    {
      title: "Profitability & growth",
      items: [
        { label: "Return on equity (ROE)", value: num(row.roe_pct, "%"), reading: readROE(row.roe_pct) },
        { label: "Net margin", value: num(row.net_margin_pct, "%"), reading: readNetMargin(row.net_margin_pct, benchmarks) },
        { label: "Revenue growth", value: signedPct(row.revenue_growth_pct), reading: readRevenueGrowth(row.revenue_growth_pct) },
      ],
    },
    {
      title: "Balance sheet & ownership",
      items: [
        {
          label: "Debt / equity",
          value: num(row.debt_to_equity, "x"),
          reading: readDebtToEquity(row.debt_to_equity, sector),
        },
        { label: "Promoter holding", value: num(row.promoter_pct, "%"), reading: readPromoterHolding(row.promoter_pct) },
      ],
    },
    {
      title: "Price performance",
      items: [
        {
          label: "Below 52-week high",
          value: num(row.pct_from_52w_high, "%"),
          reading: read52WeekHigh(row.pct_from_52w_high),
        },
        { label: "1-year return", value: signedPct(row.ret_1y_pct), reading: readOneYearReturn(row.ret_1y_pct) },
      ],
    },
  ];
}

function MetricCard({ item }: { item: MetricItem }) {
  const { reading } = item;
  return (
    <div className="metric-card" data-tone={reading?.tone}>
      <div className="metric-label">{item.label}</div>
      <div className="metric-value">{item.value}</div>
      {reading ? (
        <>
          <span className="metric-pill" data-tone={reading.tone}>
            <span aria-hidden className="metric-pill-icon">{TONE_ICON[reading.tone]}</span>
            {reading.label}
          </span>
          <p className="metric-note">{reading.note}</p>
        </>
      ) : item.hint ? (
        <p className="metric-note">{item.hint}</p>
      ) : null}
    </div>
  );
}
