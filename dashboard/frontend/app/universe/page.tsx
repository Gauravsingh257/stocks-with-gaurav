"use client";
/**
 * /universe — the full researchable NSE universe.
 *
 * A reference surface, not a decision surface: the scan tells you what to trade,
 * this tells you what exists and what each name looks like on the numbers.
 * Reads the weekly `stock_universe` snapshot, so it is a table read with no
 * provider calls on the request path.
 */
import SectionTabs from "@/components/SectionTabs";
import { RESEARCH_TABS } from "@/lib/navGroups";
import { StockUniverse } from "@/app/research/StockUniverse";

export default function UniversePage() {
  return (
    <div className="px-4 md:px-6 pt-4 pb-10">
      <SectionTabs items={RESEARCH_TABS} label="Research" />
      <StockUniverse />
    </div>
  );
}
