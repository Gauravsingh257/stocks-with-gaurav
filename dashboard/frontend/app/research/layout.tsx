import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Research Center",
  description:
    "SMC research feed with discovery, watchlist, final review ideas, NSE coverage, risk levels, and transparent scan diagnostics.",
  alternates: { canonical: "/research" },
};

export default function ResearchLayout({ children }: { children: React.ReactNode }) {
  return children;
}
