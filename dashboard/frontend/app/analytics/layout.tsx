import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Track Record",
  description: "Verified algo track record — intraday R-multiples, swing and long-term research hit rates, equity curve, and setup quality.",
  alternates: { canonical: "/analytics" },
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return <div>{children}</div>;
}
