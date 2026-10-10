import type { Metadata } from "next";
import NextBestAction from "@/components/NextBestAction";

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
        <NextBestAction context="research" className="mb-4" />
      </div>
      {children}
    </div>
  );
}
