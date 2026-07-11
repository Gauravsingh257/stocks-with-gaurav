import type { Metadata } from "next";
import SectionTabs from "@/components/SectionTabs";
import { PORTFOLIO_TABS } from "@/lib/navGroups";

export const metadata: Metadata = {
  title: "Trading Journal",
  description: "Review logged signals, trades, research ideas, notes, and trade outcomes in one journal.",
  alternates: { canonical: "/journal" },
};

export default function JournalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div className="px-4 md:px-6 pt-4">
        <SectionTabs items={PORTFOLIO_TABS} label="Portfolio" />
      </div>
      {children}
    </div>
  );
}
