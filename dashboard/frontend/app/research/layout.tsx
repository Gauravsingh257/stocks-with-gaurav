import type { Metadata } from "next";
import SectionTabs from "@/components/SectionTabs";
import { RESEARCH_TABS } from "@/lib/navGroups";

export const metadata: Metadata = {
  title: "AI Research Center",
  description:
    "SMC research feed with discovery, watchlist, final review ideas, NSE coverage, risk levels, and transparent scan diagnostics.",
  alternates: { canonical: "/research" },
};

export default function ResearchLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-4 md:px-6 pt-4">
        <SectionTabs items={RESEARCH_TABS} label="Research" />
      </div>
      {children}
    </div>
  );
}
