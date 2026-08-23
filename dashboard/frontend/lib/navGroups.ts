import type { SectionTab } from "@/components/SectionTabs";

/**
 * Section-tab definitions for the 5 consolidated nav groups. Pages that were
 * folded out of the top-level sidebar render the relevant strip so they stay
 * one click from their siblings.
 */
export const RESEARCH_TABS: SectionTab[] = [
  { href: "/research", label: "AI Research" },
  { href: "/screeners", label: "Screeners" },
  { href: "/universe", label: "Stock Universe" },
];

export const MARKETS_TABS: SectionTab[] = [
  { href: "/oi-intelligence", label: "Live OI Radar" },
  { href: "/market-intelligence", label: "Market Intel" },
];

export const PORTFOLIO_TABS: SectionTab[] = [
  { href: "/intelligence", label: "Portfolio Intel" },
  { href: "/analytics", label: "Track Record" },
  { href: "/journal", label: "Journal" },
];
