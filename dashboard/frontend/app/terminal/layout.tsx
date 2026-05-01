import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Trade Opportunity Terminal | StocksWithGaurav",
  description:
    "Live SMC trade opportunities — order blocks, fair value gaps, liquidity sweeps, and structure shifts curated as decision-ready cards.",
  alternates: { canonical: "/terminal" },
};

export default function TerminalLayout({ children }: { children: React.ReactNode }) {
  return <div className="terminal-shell">{children}</div>;
}
