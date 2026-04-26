import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Analytics",
  description: "Trading and research analytics for journal outcomes, equity curve, setup quality, and performance review.",
  alternates: { canonical: "/analytics" },
};

export default function AnalyticsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
