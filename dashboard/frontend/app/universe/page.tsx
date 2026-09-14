"use client";
/**
 * /universe — the full researchable NSE universe.
 *
 * A reference surface, not a decision surface: the scan tells you what to trade,
 * this tells you what exists and what each name looks like on the numbers.
 * Reads the weekly `stock_universe` snapshot, so it is a table read with no
 * provider calls on the request path.
 *
 * Deep links from global search (?sector=Pharma, ?q=…) are read with
 * useSearchParams, which on a statically prerendered page must sit under a
 * Suspense boundary. The table is keyed on the link, so following a new one —
 * even while already on this page — remounts it with the new filter.
 */
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import SectionTabs from "@/components/SectionTabs";
import { RESEARCH_TABS } from "@/lib/navGroups";
import { StockUniverse } from "@/app/research/StockUniverse";

function UniverseFromLink() {
  const params = useSearchParams();
  const sector = params.get("sector") ?? "";
  const query = params.get("q") ?? "";
  return <StockUniverse key={`${sector}|${query}`} initialSector={sector} initialQuery={query} />;
}

function UniverseLoading() {
  return (
    <div className="glass" style={{ padding: 14 }}>
      <div style={{ fontWeight: 600 }}>Stock Universe</div>
      <div style={{ fontSize: "0.8rem", color: "var(--text-dim)", marginTop: 6 }}>Loading…</div>
    </div>
  );
}

export default function UniversePage() {
  return (
    <div className="px-4 md:px-6 pt-4 pb-10">
      <SectionTabs items={RESEARCH_TABS} label="Research" />
      <Suspense fallback={<UniverseLoading />}>
        <UniverseFromLink />
      </Suspense>
    </div>
  );
}
