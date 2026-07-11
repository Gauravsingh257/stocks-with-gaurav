import type { Metadata } from "next";
import SectionTabs from "@/components/SectionTabs";
import { PORTFOLIO_TABS } from "@/lib/navGroups";

export const metadata: Metadata = {
  title: "Track Record",
  description: "Verified algo track record — intraday R-multiples, swing and long-term research hit rates, equity curve, and setup quality.",
  alternates: { canonical: "/analytics" },
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-4 md:px-6 pt-4">
        <SectionTabs items={PORTFOLIO_TABS} label="Portfolio" />
      </div>
      {children}
    </div>
  );
}
